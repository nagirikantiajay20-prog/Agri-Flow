"""
Admin service (Master Plan Module 11 — replaces the rest of admin-api +
admin-create-user).

`create_manager` / `reset_password` mirror the verified CORRECT pattern
already used by the legacy `admin-create-user` function: the actor's
role is checked server-side (via the `require_role(SUPER_ADMIN)`
dependency at the router layer) before anything happens — this is the
one Edge Function in the legacy system that already did identity
derivation right (Master Plan §1.1 / §1.5).
"""
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import ConflictError, NotFoundError
from app.core.security import hash_password
from app.models.crop import Crop, FarmVisit
from app.models.enums import (
    BookingStatus,
    CropStatus,
    GrainSaleStatus,
    PaymentStatus,
    TransactionStatus,
    UserRole,
    UserStatus,
)
from app.models.grain import GrainSale
from app.models.ledger import AuditLog, Transaction
from app.models.seed import Seed, SeedPurchase
from app.models.user import StaffProfile, User
from app.models.warehouse import BookingSlot, Warehouse
from app.schemas.common import PageParams
from app.services import audit_service, auth_service


async def create_manager(
    db: AsyncSession,
    *,
    creator: User,
    name: str,
    phone: str,
    email: str | None,
    password: str,
    role: UserRole,
    assigned_region: str | None,
    department: str | None,
) -> User:
    existing = await db.execute(select(User).where(User.phone == phone))
    if existing.scalar_one_or_none() is not None:
        raise ConflictError("An account with this phone number already exists")

    user = User(
        name=name,
        phone=phone,
        email=email,
        password_hash=hash_password(password),
        role=role,
        status=UserStatus.ACTIVE,  # staff accounts are active immediately, no approval gate
        first_login=True,
    )
    db.add(user)
    await db.flush()
    db.add(StaffProfile(user_id=user.id, assigned_region=assigned_region, department=department))

    await audit_service.record(
        db, actor_id=creator.id, action="manager.create", entity_type="user", entity_id=user.id,
        new_value={"role": role.value},
    )
    await db.flush()
    return user


async def list_managers(db: AsyncSession, *, params: PageParams) -> tuple[list[User], int]:
    query = (
        select(User)
        .where(User.role.in_([UserRole.MANAGER, UserRole.SUPER_ADMIN]))
        .options(selectinload(User.staff_profile))
    )
    count_query = select(func.count()).select_from(User).where(User.role.in_([UserRole.MANAGER, UserRole.SUPER_ADMIN]))
    total = (await db.execute(count_query)).scalar_one()
    query = query.order_by(User.created_at.desc()).offset((params.page - 1) * params.page_size).limit(params.page_size)
    rows = (await db.execute(query)).scalars().all()
    return list(rows), total


async def update_manager(
    db: AsyncSession,
    *,
    actor: User,
    manager_id: uuid.UUID,
    **fields,
) -> User:
    result = await db.execute(
        select(User).where(User.id == manager_id).options(selectinload(User.staff_profile))
    )
    manager = result.scalar_one_or_none()
    if manager is None or manager.role not in (UserRole.MANAGER, UserRole.SUPER_ADMIN):
        raise NotFoundError("Manager not found")

    user_fields = {"name", "phone", "email"}
    profile_fields = {"assigned_region", "department"}

    if "phone" in fields and fields["phone"] is not None and fields["phone"] != manager.phone:
        existing = await db.execute(select(User).where(User.phone == fields["phone"], User.id != manager.id))
        if existing.scalar_one_or_none() is not None:
            raise ConflictError("An account with this phone number already exists")

    old_val = {"name": manager.name, "phone": manager.phone}
    for k, v in fields.items():
        if v is not None:
            if k in user_fields:
                setattr(manager, k, v)
            elif k in profile_fields:
                if manager.staff_profile is None:
                    manager.staff_profile = StaffProfile(user_id=manager.id)
                    db.add(manager.staff_profile)
                setattr(manager.staff_profile, k, v)

    await audit_service.record(
        db,
        actor_id=actor.id,
        action="manager.update",
        entity_type="user",
        entity_id=manager.id,
        old_value=old_val,
        new_value={"name": manager.name, "phone": manager.phone},
    )
    await db.flush()
    await db.refresh(manager, ["staff_profile"])
    return manager


async def update_manager_status(db: AsyncSession, *, actor: User, manager_id: uuid.UUID, new_status: UserStatus) -> User:
    manager = await db.get(User, manager_id)
    if manager is None or manager.role not in (UserRole.MANAGER, UserRole.SUPER_ADMIN):
        raise NotFoundError("Manager not found")
    old_status = manager.status
    manager.status = new_status
    await audit_service.record(
        db, actor_id=actor.id, action="manager.status_update", entity_type="user", entity_id=manager.id,
        old_value={"status": old_status.value}, new_value={"status": new_status.value},
    )
    await db.flush()
    return manager


async def reset_manager_password(db: AsyncSession, *, actor: User, manager_id: uuid.UUID, new_password: str) -> None:
    """super_admin only — enforced at the router via require_role, mirroring
    the one legacy Edge Function that already did this correctly."""
    manager = await db.get(User, manager_id)
    if manager is None:
        raise NotFoundError("Manager not found")
    manager.password_hash = hash_password(new_password)
    await auth_service.revoke_all_sessions(db, user_id=manager.id)
    await audit_service.record(
        db, actor_id=actor.id, action="manager.password_reset", entity_type="user", entity_id=manager.id,
    )
    await db.flush()


async def list_audit_logs(db: AsyncSession, *, params: PageParams) -> tuple[list[AuditLog], int]:
    query = select(AuditLog)
    count_query = select(func.count()).select_from(AuditLog)
    total = (await db.execute(count_query)).scalar_one()
    query = (
        query.order_by(AuditLog.created_at.desc())
        .offset((params.page - 1) * params.page_size)
        .limit(params.page_size)
    )
    rows = (await db.execute(query)).scalars().all()
    return list(rows), total


async def get_dashboard(db: AsyncSession) -> dict:
    """Every scalar the operational dashboard needs in ONE round trip.

    The legacy admin dashboard issued 13 sequential queries and the first
    pass of this service issued 6; on a pooled/remote Postgres that is
    13x (resp. 6x) the network latency before the page can render, which
    is what put the dashboard over the 2s budget.
    """
    month_start = date.today().replace(day=1)
    today = date.today()

    row = (
        await db.execute(
            select(
                select(func.count())
                .select_from(User)
                .where(User.role == UserRole.FARMER)
                .scalar_subquery()
                .label("total_farmers"),
                select(func.count())
                .select_from(User)
                .where(User.role == UserRole.FARMER, User.status == UserStatus.ACTIVE)
                .scalar_subquery()
                .label("active_farmers"),
                select(func.count())
                .select_from(User)
                .where(User.role == UserRole.FARMER, User.status == UserStatus.PENDING)
                .scalar_subquery()
                .label("pending_farmer_approvals"),
                select(func.count())
                .select_from(User)
                .where(User.role == UserRole.MANAGER)
                .scalar_subquery()
                .label("total_managers"),
                select(func.count())
                .select_from(BookingSlot)
                .where(
                    BookingSlot.status.in_(
                        [BookingStatus.PENDING, BookingStatus.CONFIRMED, BookingStatus.DELIVERED]
                    )
                )
                .scalar_subquery()
                .label("active_bookings"),
                select(func.count())
                .select_from(GrainSale)
                .where(GrainSale.status.in_([GrainSaleStatus.PENDING, GrainSaleStatus.RECEIVED]))
                .scalar_subquery()
                .label("pending_grain_sales"),
                select(func.coalesce(func.sum(Seed.stock_kg), 0))
                .scalar_subquery()
                .label("total_seed_stock_kg"),
                select(func.coalesce(func.sum(Warehouse.current_load_kg), 0))
                .scalar_subquery()
                .label("warehouse_inventory_kg"),
                select(func.coalesce(func.sum(GrainSale.good_material_kg), 0))
                .where(
                    GrainSale.status.in_(
                        [GrainSaleStatus.RECEIVED, GrainSaleStatus.APPROVED, GrainSaleStatus.PAID]
                    ),
                    GrainSale.updated_at >= month_start,
                )
                .scalar_subquery()
                .label("procurement_mtd_kg"),
                select(func.coalesce(func.sum(SeedPurchase.total_amount), 0))
                .where(
                    SeedPurchase.payment_status == PaymentStatus.PAID,
                    SeedPurchase.created_at >= month_start,
                )
                .scalar_subquery()
                .label("revenue_mtd"),
                select(func.coalesce(func.sum(GrainSale.total_amount), 0))
                .where(GrainSale.status == GrainSaleStatus.PAID, GrainSale.updated_at >= month_start)
                .scalar_subquery()
                .label("procurement_cost_mtd"),
                select(func.count())
                .select_from(Transaction)
                .where(Transaction.status == TransactionStatus.PENDING)
                .scalar_subquery()
                .label("pending_payments"),
                select(func.count())
                .select_from(Crop)
                .where(Crop.status == CropStatus.GROWING)
                .scalar_subquery()
                .label("active_crops"),
                select(func.count())
                .select_from(FarmVisit)
                .where(FarmVisit.scheduled_date == today)
                .scalar_subquery()
                .label("visits_today"),
            )
        )
    ).one()

    return {
        "total_farmers": row.total_farmers,
        "active_farmers": row.active_farmers,
        "pending_farmer_approvals": row.pending_farmer_approvals,
        "total_managers": row.total_managers,
        "active_bookings": row.active_bookings,
        "pending_grain_sales": row.pending_grain_sales,
        "total_seed_stock_kg": Decimal(row.total_seed_stock_kg),
        "warehouse_inventory_kg": Decimal(row.warehouse_inventory_kg),
        "procurement_mtd_kg": Decimal(row.procurement_mtd_kg),
        "revenue_mtd": Decimal(row.revenue_mtd),
        "procurement_cost_mtd": Decimal(row.procurement_cost_mtd),
        "profit_mtd": Decimal(row.revenue_mtd) - Decimal(row.procurement_cost_mtd),
        "pending_payments": row.pending_payments,
        "active_crops": row.active_crops,
        "visits_today": row.visits_today,
    }


async def get_monthly_report(db: AsyncSession, *, month: str) -> dict:
    """`month` in 'YYYY-MM' form. NOTE: the legacy `get_admin_dashboard` /
    reporting RPC's exact aggregation logic was not present in the shipped
    migrations (Master Plan §1.3) — this is a from-scratch reimplementation
    against the reconciled schema, and should be diffed against the live
    RPC's output during Phase 0/13 before being trusted as a drop-in
    replacement."""
    year, mon = (int(p) for p in month.split("-"))
    start = date(year, mon, 1)
    end = date(year + 1, 1, 1) if mon == 12 else date(year, mon + 1, 1)

    seed_total = (
        await db.execute(
            select(func.coalesce(func.sum(SeedPurchase.total_amount), 0)).where(
                SeedPurchase.created_at >= start, SeedPurchase.created_at < end
            )
        )
    ).scalar_one()
    grain_total = (
        await db.execute(
            select(func.coalesce(func.sum(GrainSale.total_amount), 0)).where(
                GrainSale.status == GrainSaleStatus.PAID, GrainSale.created_at >= start, GrainSale.created_at < end
            )
        )
    ).scalar_one()
    bookings = (
        await db.execute(
            select(func.count()).select_from(BookingSlot).where(BookingSlot.created_at >= start, BookingSlot.created_at < end)
        )
    ).scalar_one()
    new_farmers = (
        await db.execute(
            select(func.count())
            .select_from(User)
            .where(User.role == UserRole.FARMER, User.created_at >= start, User.created_at < end)
        )
    ).scalar_one()

    return {
        "month": month,
        "total_seed_purchases_amount": Decimal(seed_total),
        "total_grain_sales_amount": Decimal(grain_total),
        "total_bookings": bookings,
        "new_farmers": new_farmers,
    }
