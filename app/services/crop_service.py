import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.crop import DEFAULT_VISIT_MONTHS, VISIT_MONTHS_BY_CROP, Crop, FarmVisit
from app.models.user import User
from app.schemas.common import PageParams
from app.services import audit_service


async def register_crop(
    db: AsyncSession,
    *,
    farmer: User,
    crop_type: str,
    acres,
    sowing_date,
    crop_name: str | None = None,
    harvest_date=None,
    stage=None,
    notes: str | None = None,
) -> Crop:
    crop = Crop(
        farmer_id=farmer.id,
        crop_type=crop_type,
        crop_name=crop_name,
        acres=acres,
        sowing_date=sowing_date,
        harvest_date=harvest_date,
        notes=notes,
    )
    if stage is not None:
        crop.stage = stage
    db.add(crop)
    await db.flush()

    # Auto-schedule farm visits — ports the verified logic from
    # agriflow-web/supabase/functions/farmer-api registerCrop exactly
    # (Master Plan Module 5), using the centralized constant instead of a
    # duplicated literal per call site.
    months = VISIT_MONTHS_BY_CROP.get(crop_type, DEFAULT_VISIT_MONTHS)
    for month in months:
        db.add(
            FarmVisit(
                crop_id=crop.id,
                farmer_id=farmer.id,
                visit_month=month,
                scheduled_date=sowing_date + timedelta(days=30 * month),
            )
        )
    await audit_service.record(db, actor_id=farmer.id, action="crop.register", entity_type="crop", entity_id=crop.id)
    await db.flush()
    return crop


async def list_crops_for_actor(
    db: AsyncSession, *, actor: User, params: PageParams | None = None, include_deleted: bool = False
) -> tuple[list[Crop], int]:
    params = params or PageParams(page=1, page_size=100)
    query = select(Crop)
    count_query = select(func.count()).select_from(Crop)
    if actor.role.value == "farmer":
        query = query.where(Crop.farmer_id == actor.id)
        count_query = count_query.where(Crop.farmer_id == actor.id)
    # manager/super_admin see all crops today; regional filtering via
    # staff_profiles.assigned_region is a deferred decision — see Master
    # Plan §5 / §14.
    if not include_deleted:
        query = query.where(Crop.deleted_at.is_(None))
        count_query = count_query.where(Crop.deleted_at.is_(None))
    if params.status:
        query = query.where(Crop.status == params.status)
        count_query = count_query.where(Crop.status == params.status)

    total = (await db.execute(count_query)).scalar_one()
    query = (
        query.order_by(Crop.created_at.desc())
        .offset((params.page - 1) * params.page_size)
        .limit(params.page_size)
    )
    return list((await db.execute(query)).scalars().all()), total


async def list_visits_for_actor(
    db: AsyncSession, *, actor: User, params: PageParams | None = None
) -> tuple[list[FarmVisit], int]:
    params = params or PageParams(page=1, page_size=100)
    query = select(FarmVisit)
    count_query = select(func.count()).select_from(FarmVisit)
    if actor.role.value == "farmer":
        query = query.where(FarmVisit.farmer_id == actor.id)
        count_query = count_query.where(FarmVisit.farmer_id == actor.id)
    if params.status:
        query = query.where(FarmVisit.status == params.status)
        count_query = count_query.where(FarmVisit.status == params.status)

    total = (await db.execute(count_query)).scalar_one()
    query = (
        query.order_by(FarmVisit.scheduled_date.asc().nullslast(), FarmVisit.created_at.desc())
        .offset((params.page - 1) * params.page_size)
        .limit(params.page_size)
    )
    return list((await db.execute(query)).scalars().all()), total


async def schedule_visit(db: AsyncSession, *, manager: User, visit_id: uuid.UUID, staff_id, scheduled_date) -> FarmVisit:
    visit = await db.get(FarmVisit, visit_id)
    if visit is None:
        raise NotFoundError("Farm visit not found")
    if staff_id is not None:
        visit.staff_id = staff_id
    if scheduled_date is not None:
        visit.scheduled_date = scheduled_date
    await audit_service.record(db, actor_id=manager.id, action="visit.schedule", entity_type="farm_visit", entity_id=visit.id)
    await db.flush()
    return visit


async def complete_visit(
    db: AsyncSession,
    *,
    manager: User,
    visit_id: uuid.UUID,
    verified_acres,
    report: str,
    diagnosis: str | None = None,
    recommendation: str | None = None,
) -> FarmVisit:
    from datetime import date

    from app.models.enums import NotificationType, VisitStatus
    from app.services import notification_service

    visit = await db.get(FarmVisit, visit_id)
    if visit is None:
        raise NotFoundError("Farm visit not found")
    visit.status = VisitStatus.COMPLETED
    visit.verified_acres = verified_acres
    visit.report = report
    visit.actual_date = visit.actual_date or date.today()
    if diagnosis is not None:
        visit.diagnosis = diagnosis
    if recommendation is not None:
        visit.recommendation = recommendation

    crop = await db.get(Crop, visit.crop_id)
    crop_label = (crop.crop_name or crop.crop_type) if crop else "your crop"
    await notification_service.notify_user(
        db,
        user_id=visit.farmer_id,
        title="Field inspection",
        message=f"Officer review for {crop_label} is now available.",
        type_=NotificationType.SUCCESS,
        reference_type="farm_visit",
        reference_id=visit.id,
    )
    await audit_service.record(db, actor_id=manager.id, action="visit.complete", entity_type="farm_visit", entity_id=visit.id)
    await db.flush()
    return visit


async def list_active_crops(
    db: AsyncSession, *, params: PageParams | None = None
) -> tuple[list[Crop], int]:
    from app.models.enums import CropStatus

    params = params or PageParams(page=1, page_size=100)
    active = (Crop.status == CropStatus.GROWING) & Crop.deleted_at.is_(None)
    base = select(Crop).where(active)
    total = (
        await db.execute(select(func.count()).select_from(Crop).where(active))
    ).scalar_one()
    rows = (
        await db.execute(
            base.order_by(Crop.created_at.desc())
            .offset((params.page - 1) * params.page_size)
            .limit(params.page_size)
        )
    ).scalars().all()
    return list(rows), total


async def create_visit(
    db: AsyncSession,
    *,
    manager: User,
    crop_id: uuid.UUID,
    visit_month: int,
    scheduled_date,
    staff_id: uuid.UUID | None,
) -> FarmVisit:
    from app.core.exceptions import ConflictError
    from app.models.enums import NotificationType
    from app.services import notification_service

    crop = await db.get(Crop, crop_id)
    if crop is None:
        raise NotFoundError("Crop not found")

    existing = await db.execute(
        select(FarmVisit).where(FarmVisit.crop_id == crop_id, FarmVisit.visit_month == visit_month)
    )
    if existing.scalar_one_or_none() is not None:
        raise ConflictError(f"A month-{visit_month} visit already exists for this crop")

    visit = FarmVisit(
        crop_id=crop_id,
        farmer_id=crop.farmer_id,
        staff_id=staff_id,
        visit_month=visit_month,
        scheduled_date=scheduled_date,
    )
    db.add(visit)
    await db.flush()

    await notification_service.notify_user(
        db,
        user_id=crop.farmer_id,
        title="Farm visit scheduled",
        message=f"A farm visit is scheduled for {scheduled_date} (month {visit_month}).",
        type_=NotificationType.INFO,
        reference_type="farm_visit",
        reference_id=visit.id,
    )
    if staff_id is not None:
        await notification_service.notify_user(
            db,
            user_id=staff_id,
            title="Farm visit assigned",
            message=f"You have been assigned a farm visit on {scheduled_date}.",
            type_=NotificationType.INFO,
            reference_type="farm_visit",
            reference_id=visit.id,
        )

    await audit_service.record(
        db, actor_id=manager.id, action="visit.create", entity_type="farm_visit", entity_id=visit.id,
        new_value={"crop_id": str(crop_id), "scheduled_date": str(scheduled_date)},
    )
    await db.flush()
    return visit


async def send_visit_reminders(db: AsyncSession, *, actor: User, days_ahead: int = 2) -> int:
    from datetime import date

    from sqlalchemy import exists

    from app.models.enums import NotificationType, VisitStatus
    from app.models.ledger import Notification
    from app.services import notification_service

    target = date.today() + timedelta(days=days_ahead)

    already_notified = exists().where(
        Notification.reference_type == "farm_visit",
        Notification.reference_id == FarmVisit.id,
        Notification.user_id == FarmVisit.farmer_id,
        Notification.title == "Upcoming farm visit",
    )
    result = await db.execute(
        select(FarmVisit).where(
            FarmVisit.status == VisitStatus.SCHEDULED,
            FarmVisit.scheduled_date == target,
            ~already_notified,
        )
    )
    visits = list(result.scalars().all())

    for visit in visits:
        await notification_service.notify_user(
            db,
            user_id=visit.farmer_id,
            title="Upcoming farm visit",
            message=f"Reminder: your farm visit is scheduled for {visit.scheduled_date}.",
            type_=NotificationType.INFO,
            reference_type="farm_visit",
            reference_id=visit.id,
        )

    if visits:
        await audit_service.record(
            db, actor_id=actor.id if actor else None, action="visit.reminders_sent",
            entity_type="farm_visit", details=f"{len(visits)} reminder(s) for {target}",
        )
    await db.flush()
    return len(visits)


async def get_own_crop(db: AsyncSession, *, farmer: User, crop_id: uuid.UUID) -> Crop:
    """Ownership is part of the lookup itself, so another farmer's crop is
    indistinguishable from a nonexistent one (no existence leak). Returns a
    soft-deleted crop too — read-only history views (inspections) still
    need to resolve it."""
    result = await db.execute(select(Crop).where(Crop.id == crop_id, Crop.farmer_id == farmer.id))
    crop = result.scalar_one_or_none()
    if crop is None:
        raise NotFoundError("Crop not found")
    return crop


async def get_own_active_crop(db: AsyncSession, *, farmer: User, crop_id: uuid.UUID) -> Crop:
    """Like get_own_crop, but rejects a soft-deleted crop — for any action
    that would mutate the crop or attach a new record to it (edit, scan).
    Callers that also do external I/O (e.g. uploading a scan image) should
    call this BEFORE that I/O, so a deleted crop is rejected without
    wasting the upload."""
    from app.core.exceptions import ConflictError

    crop = await get_own_crop(db, farmer=farmer, crop_id=crop_id)
    if crop.deleted_at is not None:
        raise ConflictError("This crop has been deleted and can no longer be edited")
    return crop


async def update_own_crop(db: AsyncSession, *, farmer: User, crop_id: uuid.UUID, updates: dict) -> Crop:
    crop = await get_own_active_crop(db, farmer=farmer, crop_id=crop_id)
    old = {k: str(getattr(crop, k)) for k in updates}
    for field, value in updates.items():
        setattr(crop, field, value)
    if crop.harvest_date and crop.harvest_date < crop.sowing_date:
        from app.core.exceptions import ValidationError

        raise ValidationError("harvest_date cannot be before sowing_date")
    await audit_service.record(
        db, actor_id=farmer.id, action="crop.update", entity_type="crop", entity_id=crop.id,
        old_value=old, new_value={k: str(v) for k, v in updates.items()},
    )
    await db.flush()
    return crop


async def delete_own_crop(db: AsyncSession, *, farmer: User, crop_id: uuid.UUID) -> Crop:
    """Soft delete: the crop, its farm visits/inspections, and any linked
    grain sales are never physically removed — Farmer business records must
    survive deletion for history/audit purposes. Marking `deleted_at` hides
    the crop from active lists (list_crops_for_actor / list_active_crops)
    while `include_closed=True` history views and direct-by-id lookups
    (inspections, audit) still find it. Idempotent: deleting an
    already-deleted crop is a no-op, not an error, so a retried request
    from a flaky mobile connection never surfaces a spurious failure."""
    from datetime import datetime, timezone

    crop = await get_own_crop(db, farmer=farmer, crop_id=crop_id)
    if crop.deleted_at is None:
        crop.deleted_at = datetime.now(timezone.utc)
        await audit_service.record(
            db, actor_id=farmer.id, action="crop.delete", entity_type="crop", entity_id=crop_id
        )
        await db.flush()
    return crop


async def list_own_visits(
    db: AsyncSession, *, farmer: User, crop_id: uuid.UUID | None = None
) -> list[FarmVisit]:
    query = select(FarmVisit).where(FarmVisit.farmer_id == farmer.id)
    if crop_id is not None:
        query = query.where(FarmVisit.crop_id == crop_id)
    query = query.order_by(FarmVisit.scheduled_date.asc().nullslast(), FarmVisit.created_at.desc())
    return list((await db.execute(query)).scalars().all())


def _visit_month_for(crop: Crop, on_day) -> int:
    months = (on_day - crop.sowing_date).days // 30 + 1
    return max(1, min(6, months))


async def submit_scan(
    db: AsyncSession, *, farmer: User, crop_id: uuid.UUID, image_path: str, notes: str | None
) -> FarmVisit:
    from datetime import date

    from app.models.enums import NotificationType, UserRole, VisitStatus
    from app.services import notification_service

    # Re-check here too (defense in depth) even though the router already
    # calls get_own_active_crop before uploading the image — this function
    # must stay safe to call on its own.
    crop = await get_own_active_crop(db, farmer=farmer, crop_id=crop_id)
    today = date.today()
    visit = FarmVisit(
        crop_id=crop.id,
        farmer_id=farmer.id,
        visit_month=_visit_month_for(crop, today),
        scheduled_date=today,
        actual_date=today,
        status=VisitStatus.PENDING_REVIEW,
        notes=notes,
        image_path=image_path,
    )
    db.add(visit)
    await db.flush()

    await notification_service.notify_roles(
        db,
        roles=[UserRole.MANAGER, UserRole.SUPER_ADMIN],
        title="Crop scan awaiting review",
        message=f"{farmer.name} submitted a field scan for {crop.crop_name or crop.crop_type}.",
        type_=NotificationType.INFO,
        reference_type="farm_visit",
        reference_id=visit.id,
    )
    await audit_service.record(
        db, actor_id=farmer.id, action="crop.scan", entity_type="farm_visit", entity_id=visit.id
    )
    await db.flush()
    return visit
