import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import ConflictError, NotFoundError
from app.models.enums import BankRequestStatus, NotificationType, UserRole, UserStatus
from app.models.ledger import BankChangeRequest
from app.models.user import FarmerProfile, User
from app.schemas.common import PageParams
from app.services import audit_service, notification_service


async def get_own_profile(db: AsyncSession, *, user: User) -> tuple[User, FarmerProfile]:
    result = await db.execute(select(FarmerProfile).where(FarmerProfile.user_id == user.id))
    profile = result.scalar_one_or_none()
    if profile is None:
        raise NotFoundError("Farmer profile not found")
    return user, profile


async def update_own_profile(db: AsyncSession, *, user: User, updates: dict) -> FarmerProfile:
    result = await db.execute(select(FarmerProfile).where(FarmerProfile.user_id == user.id))
    profile = result.scalar_one_or_none()
    if profile is None:
        raise NotFoundError("Farmer profile not found")
    for field, value in updates.items():
        if value is not None:
            setattr(profile, field, value)
    await db.flush()
    return profile


async def request_bank_change(
    db: AsyncSession, *, farmer: User, bank_name: str, account_number: str, ifsc_code: str, upi_id: str | None
) -> BankChangeRequest:
    # Fix vs. the legacy behaviour (Master Plan Module 4): block a second
    # request while one is already pending, rather than silently allowing
    # unlimited pending requests to pile up.
    existing = await db.execute(
        select(BankChangeRequest).where(
            BankChangeRequest.farmer_id == farmer.id,
            BankChangeRequest.status == BankRequestStatus.PENDING,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise ConflictError("A bank change request is already pending review")

    req = BankChangeRequest(
        farmer_id=farmer.id,
        bank_name=bank_name,
        account_number=account_number,
        ifsc_code=ifsc_code,
        upi_id=upi_id,
    )
    db.add(req)
    await db.flush()

    await notification_service.notify_roles(
        db,
        roles=[UserRole.MANAGER, UserRole.SUPER_ADMIN],
        title="New bank change request",
        message=f"{farmer.name} requested a bank detail change.",
        type_=NotificationType.INFO,
        reference_type="bank_change_request",
        reference_id=req.id,
    )
    await audit_service.record(
        db, actor_id=farmer.id, action="bank_change.request", entity_type="bank_change_request", entity_id=req.id
    )
    return req


async def review_bank_change(
    db: AsyncSession, *, reviewer: User, request_id: uuid.UUID, approve: bool, admin_notes: str | None
) -> BankChangeRequest:
    req = await db.get(BankChangeRequest, request_id)
    if req is None:
        raise NotFoundError("Bank change request not found")
    if req.status != BankRequestStatus.PENDING:
        raise ConflictError("This request has already been reviewed")

    req.status = BankRequestStatus.APPROVED if approve else BankRequestStatus.REJECTED
    req.admin_notes = admin_notes
    req.reviewed_at = datetime.now(timezone.utc)
    req.reviewed_by = reviewer.id

    if approve:
        profile_result = await db.execute(select(FarmerProfile).where(FarmerProfile.user_id == req.farmer_id))
        profile = profile_result.scalar_one_or_none()
        if profile:
            profile.bank_name = req.bank_name
            profile.account_number = req.account_number
            profile.ifsc_code = req.ifsc_code
            profile.upi_id = req.upi_id

    await notification_service.notify_user(
        db,
        user_id=req.farmer_id,
        title="Bank change request reviewed",
        message=f"Your bank change request was {'approved' if approve else 'rejected'}.",
        type_=NotificationType.SUCCESS if approve else NotificationType.WARNING,
        reference_type="bank_change_request",
        reference_id=req.id,
    )
    await audit_service.record(
        db,
        actor_id=reviewer.id,
        action="bank_change.review",
        entity_type="bank_change_request",
        entity_id=req.id,
        new_value={"status": req.status.value},
    )
    await db.flush()
    return req


async def load_farmer_with_profile(db: AsyncSession, *, farmer_id: uuid.UUID) -> User | None:
    result = await db.execute(
        select(User).where(User.id == farmer_id).options(selectinload(User.farmer_profile))
    )
    return result.scalar_one_or_none()


async def list_farmers(db: AsyncSession, *, params: PageParams) -> tuple[list[User], int]:
    query = select(User).where(User.role == UserRole.FARMER).options(selectinload(User.farmer_profile))
    count_query = select(func.count()).select_from(User).where(User.role == UserRole.FARMER)

    if params.status:
        query = query.where(User.status == params.status)
        count_query = count_query.where(User.status == params.status)
    if params.search:
        pattern = f"%{params.search}%"
        query = query.where(User.name.ilike(pattern) | User.phone.ilike(pattern))
        count_query = count_query.where(User.name.ilike(pattern) | User.phone.ilike(pattern))

    total = (await db.execute(count_query)).scalar_one()
    sort_col = getattr(User, params.sort, User.created_at)
    query = query.order_by(sort_col.desc() if params.order == "desc" else sort_col.asc())
    query = query.offset((params.page - 1) * params.page_size).limit(params.page_size)

    farmers = (await db.execute(query)).scalars().all()
    return list(farmers), total


async def set_approval(
    db: AsyncSession, *, reviewer: User, farmer_id: uuid.UUID, new_status: UserStatus
) -> User:
    farmer = await load_farmer_with_profile(db, farmer_id=farmer_id)
    if farmer is None or farmer.role != UserRole.FARMER:
        raise NotFoundError("Farmer not found")

    old_status = farmer.status
    farmer.status = new_status
    await audit_service.record(
        db,
        actor_id=reviewer.id,
        action="farmer.approval",
        entity_type="user",
        entity_id=farmer.id,
        old_value={"status": old_status.value},
        new_value={"status": new_status.value},
    )
    await notification_service.notify_user(
        db,
        user_id=farmer.id,
        title="Account status updated",
        message=f"Your account is now {new_status.value}.",
        type_=NotificationType.SUCCESS if new_status == UserStatus.ACTIVE else NotificationType.WARNING,
    )
    await db.flush()
    return farmer


async def get_farmer_detail(db: AsyncSession, *, farmer_id: uuid.UUID) -> tuple[User, FarmerProfile | None]:
    farmer = await load_farmer_with_profile(db, farmer_id=farmer_id)
    if farmer is None or farmer.role != UserRole.FARMER:
        raise NotFoundError("Farmer not found")
    return farmer, farmer.farmer_profile


async def create_farmer(
    db: AsyncSession,
    *,
    creator: User,
    name: str,
    phone: str,
    email: str | None,
    password: str,
    address: str | None,
    acres_of_land,
    crop_address: str | None,
) -> User:
    from decimal import Decimal

    from app.core.security import hash_password

    existing = await db.execute(select(User).where(User.phone == phone))
    if existing.scalar_one_or_none() is not None:
        raise ConflictError("An account with this phone number already exists")

    farmer = User(
        name=name,
        phone=phone,
        email=email,
        password_hash=hash_password(password),
        role=UserRole.FARMER,
        status=UserStatus.ACTIVE,
        first_login=True,
    )
    db.add(farmer)
    await db.flush()
    db.add(
        FarmerProfile(
            user_id=farmer.id,
            address=address,
            acres_of_land=acres_of_land if acres_of_land is not None else Decimal("0"),
            crop_address=crop_address,
        )
    )
    await audit_service.record(
        db, actor_id=creator.id, action="farmer.create", entity_type="user", entity_id=farmer.id,
    )
    await db.flush()
    await db.refresh(farmer, ["farmer_profile"])
    return farmer


async def update_farmer_by_admin(
    db: AsyncSession,
    *,
    actor: User,
    farmer_id: uuid.UUID,
    **fields,
) -> User:
    farmer = await load_farmer_with_profile(db, farmer_id=farmer_id)
    if farmer is None or farmer.role != UserRole.FARMER:
        raise NotFoundError("Farmer not found")

    user_fields = {"name", "phone", "email"}
    profile_fields = {
        "address", "farm_name", "village", "district", "state", "acres_of_land",
        "crop_address", "soil_type", "irrigation_type", "primary_crop", "secondary_crop",
    }

    if "phone" in fields and fields["phone"] is not None and fields["phone"] != farmer.phone:
        existing = await db.execute(select(User).where(User.phone == fields["phone"], User.id != farmer.id))
        if existing.scalar_one_or_none() is not None:
            raise ConflictError("An account with this phone number already exists")

    old_val = {"name": farmer.name, "phone": farmer.phone}
    for k, v in fields.items():
        if v is not None:
            if k in user_fields:
                setattr(farmer, k, v)
            elif k in profile_fields:
                if farmer.farmer_profile is None:
                    farmer.farmer_profile = FarmerProfile(user_id=farmer.id)
                    db.add(farmer.farmer_profile)
                setattr(farmer.farmer_profile, k, v)

    await audit_service.record(
        db,
        actor_id=actor.id,
        action="farmer.admin_update",
        entity_type="user",
        entity_id=farmer.id,
        old_value=old_val,
        new_value={"name": farmer.name, "phone": farmer.phone},
    )
    await db.flush()
    await db.refresh(farmer, ["farmer_profile"])
    return farmer


async def list_bank_change_requests(
    db: AsyncSession, *, params: PageParams
) -> tuple[list[BankChangeRequest], int]:
    query = select(BankChangeRequest)
    count_query = select(func.count()).select_from(BankChangeRequest)
    if params.status:
        query = query.where(BankChangeRequest.status == params.status)
        count_query = count_query.where(BankChangeRequest.status == params.status)

    total = (await db.execute(count_query)).scalar_one()
    query = (
        query.order_by(BankChangeRequest.requested_at.desc())
        .offset((params.page - 1) * params.page_size)
        .limit(params.page_size)
    )
    rows = (await db.execute(query)).scalars().all()
    return list(rows), total


async def get_farmer_dashboard(db: AsyncSession, *, farmer: User) -> dict:
    from decimal import Decimal

    from app.models.crop import Crop, FarmVisit
    from app.models.enums import (
        CropStatus,
        PaymentStatus,
        TransactionDirection,
        TransactionStatus,
        VisitStatus,
    )
    from app.models.ledger import Notification, Transaction
    from app.models.seed import SeedPurchase

    totals = (
        await db.execute(
            select(
                select(func.count())
                .select_from(Crop)
                .where(Crop.farmer_id == farmer.id, Crop.status == CropStatus.GROWING)
                .scalar_subquery()
                .label("active_crops"),
                select(func.coalesce(func.sum(Transaction.amount), 0))
                .where(
                    Transaction.farmer_id == farmer.id,
                    Transaction.direction == TransactionDirection.CREDIT,
                    Transaction.status == TransactionStatus.COMPLETED,
                )
                .scalar_subquery()
                .label("total_earned"),
                select(func.coalesce(func.sum(SeedPurchase.total_amount), 0))
                .where(
                    SeedPurchase.farmer_id == farmer.id,
                    SeedPurchase.payment_status == PaymentStatus.PAID,
                )
                .scalar_subquery()
                .label("total_spent"),
                select(func.count())
                .select_from(Notification)
                .where(Notification.user_id == farmer.id, Notification.is_read.is_(False))
                .scalar_subquery()
                .label("unread_notifications"),
            )
        )
    ).one()

    recent_crops = (
        await db.execute(
            select(Crop).where(Crop.farmer_id == farmer.id).order_by(Crop.created_at.desc()).limit(5)
        )
    ).scalars().all()
    recent_transactions = (
        await db.execute(
            select(Transaction)
            .where(Transaction.farmer_id == farmer.id)
            .order_by(Transaction.created_at.desc())
            .limit(5)
        )
    ).scalars().all()
    upcoming_visits = (
        await db.execute(
            select(FarmVisit)
            .where(FarmVisit.farmer_id == farmer.id, FarmVisit.status == VisitStatus.SCHEDULED)
            .order_by(FarmVisit.scheduled_date.asc())
            .limit(3)
        )
    ).scalars().all()

    return {
        "active_crops": totals.active_crops,
        "total_earned": Decimal(totals.total_earned),
        "total_spent": Decimal(totals.total_spent),
        "unread_notifications": totals.unread_notifications,
        "recent_crops": list(recent_crops),
        "recent_transactions": list(recent_transactions),
        "upcoming_visits": list(upcoming_visits),
    }
