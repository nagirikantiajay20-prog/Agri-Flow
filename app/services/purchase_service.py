"""
Seed & Seed Purchase service (Master Plan Module 6 — concurrency-critical).

`purchase_seeds` ports the verified sequence from the legacy PostgreSQL
RPC of the same name (Master Plan §8): lock seed row -> validate stock ->
decrement -> insert purchase -> insert ledger transaction -> notify ->
audit, all in one DB transaction. The `SELECT ... FOR UPDATE` here is
the same pessimistic-locking behavior confirmed present in the shipped
migrations and MUST be preserved — removing it re-introduces the exact
oversell race the legacy schema was built to prevent (Master Plan §1.3 /
§9 / §39).
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, InsufficientStockError, NotFoundError
from app.models.enums import (
    NotificationType,
    PaymentStatus,
    TransactionDirection,
    TransactionReferenceType,
    TransactionStatus,
)
from app.models.ledger import Transaction
from app.models.seed import Seed, SeedPurchase
from app.models.user import User
from app.schemas.common import PageParams
from app.services import audit_service, notification_service


def _generate_invoice_number(prefix: str) -> str:
    return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"


async def list_seeds(db: AsyncSession, *, active_only: bool = True) -> list[Seed]:
    query = select(Seed)
    if active_only:
        query = query.where(Seed.is_active.is_(True))
    result = await db.execute(query.order_by(Seed.name))
    return list(result.scalars().all())


async def create_seed(db: AsyncSession, *, admin: User, **fields) -> Seed:
    seed = Seed(**fields)
    db.add(seed)
    await db.flush()
    await audit_service.record(db, actor_id=admin.id, action="seed.create", entity_type="seed", entity_id=seed.id)
    return seed


async def update_seed(db: AsyncSession, *, admin: User, seed_id: uuid.UUID, **fields) -> Seed:
    seed = await db.get(Seed, seed_id)
    if seed is None:
        raise NotFoundError("Seed not found")
    for k, v in fields.items():
        if v is not None:
            setattr(seed, k, v)
    await audit_service.record(db, actor_id=admin.id, action="seed.update", entity_type="seed", entity_id=seed.id)
    await db.flush()
    return seed


async def delete_seed(db: AsyncSession, *, admin: User, seed_id: uuid.UUID) -> None:
    seed = await db.get(Seed, seed_id)
    if seed is None:
        raise NotFoundError("Seed not found")
    seed.is_active = False  # soft delete — preserves purchase history integrity
    await audit_service.record(db, actor_id=admin.id, action="seed.delete", entity_type="seed", entity_id=seed.id)
    await db.flush()


async def purchase_seeds(
    db: AsyncSession,
    *,
    farmer: User,
    seed_id: uuid.UUID,
    quantity_kg: Decimal,
    warehouse_id: uuid.UUID | None,
    payment_method: str | None,
    upi_id: str | None,
    grade: str | None = None,
    pickup_date=None,
) -> SeedPurchase:
    # 1. Lock the seed row — FOR UPDATE, confirmed present in the legacy
    #    `purchase_seeds` RPC's migrations. Any other concurrent purchase
    #    of this seed blocks here until this transaction commits/rolls back.
    result = await db.execute(select(Seed).where(Seed.id == seed_id).with_for_update())
    seed = result.scalar_one_or_none()
    if seed is None:
        raise NotFoundError("Seed not found")
    if not seed.is_active:
        raise ConflictError("This seed is no longer available for purchase")

    # 2. Validate stock.
    if seed.stock_kg < quantity_kg:
        raise InsufficientStockError(
            f"Only {seed.stock_kg} kg of '{seed.name}' remain in stock",
            details={"available_kg": str(seed.stock_kg), "requested_kg": str(quantity_kg)},
        )

    # 3. Move the quantity from sellable stock onto hold until the
    #    order is paid for and collected (or fails and is restocked).
    seed.stock_kg -= quantity_kg
    seed.on_hold_kg = (seed.on_hold_kg or Decimal("0")) + quantity_kg

    # 4. Insert purchase (Decimal arithmetic throughout — Master Plan §20).
    total_amount = (seed.price_per_kg * quantity_kg).quantize(Decimal("0.01"))
    purchase = SeedPurchase(
        farmer_id=farmer.id,
        seed_id=seed.id,
        warehouse_id=warehouse_id,
        quantity_kg=quantity_kg,
        price_per_kg=seed.price_per_kg,
        total_amount=total_amount,
        payment_method=payment_method,
        upi_id=upi_id,
        grade=grade,
        pickup_date=pickup_date,
        payment_status=PaymentStatus.PENDING,
        invoice_number=_generate_invoice_number("SP"),
    )
    db.add(purchase)
    await db.flush()

    # 5. Insert ledger transaction (debit — money leaving the farmer's side).
    db.add(
        Transaction(
            reference_type=TransactionReferenceType.SEED_PURCHASE,
            reference_id=purchase.id,
            farmer_id=farmer.id,
            amount=total_amount,
            direction=TransactionDirection.DEBIT,
            status=TransactionStatus.PENDING,
            description=f"Seed purchase: {seed.name} ({quantity_kg} kg)",
            invoice_number=purchase.invoice_number,
        )
    )

    # 6. Notify.
    await notification_service.notify_user(
        db,
        user_id=farmer.id,
        title="Seed purchase submitted",
        message=f"Your purchase of {quantity_kg} kg of {seed.name} is pending payment confirmation.",
        type_=NotificationType.INFO,
        reference_type="seed_purchase",
        reference_id=purchase.id,
    )

    # 7. Audit.
    await audit_service.record(
        db,
        actor_id=farmer.id,
        action="seed_purchase.create",
        entity_type="seed_purchase",
        entity_id=purchase.id,
        new_value={"seed_id": str(seed.id), "quantity_kg": str(quantity_kg), "total_amount": str(total_amount)},
    )

    # 8. COMMIT happens at the request boundary (app.core.database.get_db) —
    #    no partial writes: if anything above raises, the whole request's
    #    transaction rolls back.
    await db.flush()
    return purchase


async def list_purchases_for_actor(db: AsyncSession, *, actor: User, params: PageParams) -> tuple[list[SeedPurchase], int]:
    query = select(SeedPurchase)
    count_query = select(func.count()).select_from(SeedPurchase)
    if actor.role.value == "farmer":
        query = query.where(SeedPurchase.farmer_id == actor.id)
        count_query = count_query.where(SeedPurchase.farmer_id == actor.id)
    if params.status:
        query = query.where(SeedPurchase.payment_status == params.status)
        count_query = count_query.where(SeedPurchase.payment_status == params.status)

    total = (await db.execute(count_query)).scalar_one()
    query = query.order_by(SeedPurchase.created_at.desc()).offset((params.page - 1) * params.page_size).limit(params.page_size)
    rows = (await db.execute(query)).scalars().all()
    return list(rows), total


async def update_purchase_status(
    db: AsyncSession, *, admin: User, purchase_id: uuid.UUID, new_status: PaymentStatus
) -> SeedPurchase:
    result = await db.execute(select(SeedPurchase).where(SeedPurchase.id == purchase_id).with_for_update())
    purchase = result.scalar_one_or_none()
    if purchase is None:
        raise NotFoundError("Seed purchase not found")
    old_status = purchase.payment_status
    if old_status != PaymentStatus.PENDING and new_status != old_status:
        raise ConflictError(f"A {old_status.value} purchase cannot be moved to {new_status.value}")
    purchase.payment_status = new_status

    if old_status == PaymentStatus.PENDING and new_status != PaymentStatus.PENDING:
        seed = (await db.execute(select(Seed).where(Seed.id == purchase.seed_id).with_for_update())).scalar_one()
        seed.on_hold_kg = max(Decimal("0"), (seed.on_hold_kg or Decimal("0")) - purchase.quantity_kg)
        if new_status == PaymentStatus.FAILED:
            seed.stock_kg += purchase.quantity_kg

    if new_status == PaymentStatus.PAID:
        await db.execute(
            Transaction.__table__.update()
            .where(
                Transaction.reference_type == TransactionReferenceType.SEED_PURCHASE,
                Transaction.reference_id == purchase.id,
            )
            .values(status=TransactionStatus.COMPLETED)
        )

    await audit_service.record(
        db,
        actor_id=admin.id,
        action="seed_purchase.status_update",
        entity_type="seed_purchase",
        entity_id=purchase.id,
        old_value={"payment_status": old_status.value},
        new_value={"payment_status": new_status.value},
    )
    await db.flush()
    return purchase


async def list_seeds_filtered(
    db: AsyncSession,
    *,
    q: str | None = None,
    crop_type: str | None = None,
    min_price: Decimal | None = None,
    max_price: Decimal | None = None,
    warehouse_id: uuid.UUID | None = None,
    in_stock_only: bool = False,
) -> list[Seed]:
    query = select(Seed).where(Seed.is_active.is_(True))
    if q:
        pattern = f"%{q.strip()}%"
        query = query.where(Seed.name.ilike(pattern) | Seed.variety.ilike(pattern))
    if crop_type and crop_type.lower() != "all":
        query = query.where(func.lower(Seed.crop_type) == crop_type.lower())
    if min_price is not None:
        query = query.where(Seed.price_per_kg >= min_price)
    if max_price is not None:
        query = query.where(Seed.price_per_kg <= max_price)
    if warehouse_id is not None:
        query = query.where(Seed.warehouse_id == warehouse_id)
    if in_stock_only:
        query = query.where(Seed.stock_kg > 0)
    return list((await db.execute(query.order_by(Seed.name))).scalars().all())
