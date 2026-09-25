"""
Ledger / Transactions service (Master Plan Module 9).

Deliberately read-only from the API's perspective — there is no
`POST /transactions` endpoint. Rows are created only as a side effect of
app.services.purchase_service.purchase_seeds and
app.services.grain_sale_service.pay_grain_sale. This closes off the
class of abuse that exists today in agriflow-web's ledgerService.js,
which writes `transactions` directly from the frontend.
"""
import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, InvalidStateTransitionError, NotFoundError
from app.models.enums import (
    NotificationType,
    PaymentStatus,
    TransactionReferenceType,
    TransactionStatus,
)
from app.models.ledger import Transaction
from app.models.seed import SeedPurchase
from app.models.user import User
from app.schemas.common import PageParams
from app.services import audit_service, notification_service


async def list_transactions_for_actor(
    db: AsyncSession,
    *,
    actor: User,
    params: PageParams,
    from_date: date | None = None,
    to_date: date | None = None,
) -> tuple[list[Transaction], int]:
    query = select(Transaction)
    count_query = select(func.count()).select_from(Transaction)
    if actor.role.value == "farmer":
        query = query.where(Transaction.farmer_id == actor.id)
        count_query = count_query.where(Transaction.farmer_id == actor.id)

    if from_date:
        query = query.where(func.date(Transaction.created_at) >= from_date)
        count_query = count_query.where(func.date(Transaction.created_at) >= from_date)
    if to_date:
        query = query.where(func.date(Transaction.created_at) <= to_date)
        count_query = count_query.where(func.date(Transaction.created_at) <= to_date)

    total = (await db.execute(count_query)).scalar_one()
    query = (
        query.order_by(Transaction.created_at.desc())
        .offset((params.page - 1) * params.page_size)
        .limit(params.page_size)
    )
    rows = (await db.execute(query)).scalars().all()
    return list(rows), total


async def mark_transaction_paid(
    db: AsyncSession, *, actor: User, transaction_id: uuid.UUID, description: str | None
) -> Transaction:
    result = await db.execute(
        select(Transaction).where(Transaction.id == transaction_id).with_for_update()
    )
    txn = result.scalar_one_or_none()
    if txn is None:
        raise NotFoundError("Transaction not found")
    if txn.status == TransactionStatus.COMPLETED:
        raise ConflictError("This transaction has already been completed")
    if txn.status == TransactionStatus.FAILED:
        raise InvalidStateTransitionError(
            "A failed transaction cannot be marked completed",
            details={"from": txn.status.value, "to": TransactionStatus.COMPLETED.value},
        )

    txn.status = TransactionStatus.COMPLETED
    if description:
        txn.description = description

    if txn.reference_type == TransactionReferenceType.SEED_PURCHASE and txn.reference_id:
        await db.execute(
            SeedPurchase.__table__.update()
            .where(SeedPurchase.id == txn.reference_id)
            .values(payment_status=PaymentStatus.PAID)
        )

    await notification_service.notify_user(
        db,
        user_id=txn.farmer_id,
        title="Payment processed",
        message=f"A payment of {txn.amount} has been processed.",
        type_=NotificationType.SUCCESS,
        reference_type="transaction",
        reference_id=txn.id,
    )
    await audit_service.record(
        db, actor_id=actor.id, action="transaction.pay", entity_type="transaction", entity_id=txn.id,
        new_value={"status": txn.status.value, "amount": str(txn.amount)},
    )
    await db.flush()
    return txn


async def get_public_stats(db: AsyncSession) -> dict:
    from app.models.crop import Crop
    from app.models.enums import UserRole, UserStatus
    from app.models.seed import Seed
    from app.models.warehouse import Warehouse

    row = (
        await db.execute(
            select(
                select(func.count())
                .select_from(User)
                .where(User.role == UserRole.FARMER, User.status == UserStatus.ACTIVE)
                .scalar_subquery()
                .label("farmers"),
                select(func.count()).select_from(Crop).scalar_subquery().label("crops"),
                select(func.count())
                .select_from(Seed)
                .where(Seed.is_active.is_(True))
                .scalar_subquery()
                .label("seeds"),
                select(func.count())
                .select_from(Warehouse)
                .where(Warehouse.is_active.is_(True))
                .scalar_subquery()
                .label("warehouses"),
            )
        )
    ).one()
    return {
        "farmers": row.farmers,
        "crops": row.crops,
        "seeds": row.seeds,
        "warehouses": row.warehouses,
    }
