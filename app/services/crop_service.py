import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.crop import DEFAULT_VISIT_MONTHS, VISIT_MONTHS_BY_CROP, Crop, FarmVisit
from app.models.user import User
from app.schemas.common import PageParams
from app.services import audit_service


async def register_crop(db: AsyncSession, *, farmer: User, crop_type: str, acres, sowing_date) -> Crop:
    crop = Crop(farmer_id=farmer.id, crop_type=crop_type, acres=acres, sowing_date=sowing_date)
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
    db: AsyncSession, *, actor: User, params: PageParams | None = None
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


async def complete_visit(db: AsyncSession, *, manager: User, visit_id: uuid.UUID, verified_acres, report: str) -> FarmVisit:
    from app.models.enums import VisitStatus

    visit = await db.get(FarmVisit, visit_id)
    if visit is None:
        raise NotFoundError("Farm visit not found")
    visit.status = VisitStatus.COMPLETED
    visit.verified_acres = verified_acres
    visit.report = report
    await audit_service.record(db, actor_id=manager.id, action="visit.complete", entity_type="farm_visit", entity_id=visit.id)
    await db.flush()
    return visit


async def list_active_crops(
    db: AsyncSession, *, params: PageParams | None = None
) -> tuple[list[Crop], int]:
    from app.models.enums import CropStatus

    params = params or PageParams(page=1, page_size=100)
    base = select(Crop).where(Crop.status == CropStatus.GROWING)
    total = (
        await db.execute(
            select(func.count()).select_from(Crop).where(Crop.status == CropStatus.GROWING)
        )
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
