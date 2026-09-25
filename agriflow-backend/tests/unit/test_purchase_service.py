from decimal import Decimal

import pytest

from app.core.exceptions import ConflictError, InsufficientStockError, NotFoundError
from app.models.enums import PaymentStatus
from app.services import purchase_service

pytestmark = pytest.mark.asyncio


async def test_create_update_delete_seed(db_session, super_admin):
    seed = await purchase_service.create_seed(
        db_session, admin=super_admin, name="Hybrid Rice", variety="HR-1",
        price_per_kg=Decimal("45.00"), stock_kg=Decimal("200"), description=None, image_url=None,
    )
    await db_session.commit()
    assert seed.is_active is True

    updated = await purchase_service.update_seed(db_session, admin=super_admin, seed_id=seed.id, price_per_kg=Decimal("50.00"))
    assert updated.price_per_kg == Decimal("50.00")

    await purchase_service.delete_seed(db_session, admin=super_admin, seed_id=seed.id)
    await db_session.commit()

    active = await purchase_service.list_seeds(db_session, active_only=True)
    assert seed.id not in {s.id for s in active}

    everything = await purchase_service.list_seeds(db_session, active_only=False)
    assert seed.id in {s.id for s in everything}  # soft-deleted, not gone


async def test_purchase_seeds_insufficient_stock_rejected(db_session, active_farmer, super_admin):
    seed = await purchase_service.create_seed(
        db_session, admin=super_admin, name="Scarce Seed", variety=None,
        price_per_kg=Decimal("10.00"), stock_kg=Decimal("5"), description=None, image_url=None,
    )
    await db_session.commit()

    with pytest.raises(InsufficientStockError):
        await purchase_service.purchase_seeds(
            db_session, farmer=active_farmer, seed_id=seed.id, quantity_kg=Decimal("10"),
            warehouse_id=None, payment_method=None, upi_id=None,
        )


async def test_purchase_seeds_computes_total_and_generates_invoice(db_session, active_farmer, super_admin):
    seed = await purchase_service.create_seed(
        db_session, admin=super_admin, name="Priced Seed", variety=None,
        price_per_kg=Decimal("12.50"), stock_kg=Decimal("100"), description=None, image_url=None,
    )
    await db_session.commit()

    purchase = await purchase_service.purchase_seeds(
        db_session, farmer=active_farmer, seed_id=seed.id, quantity_kg=Decimal("4"),
        warehouse_id=None, payment_method="upi", upi_id="test@upi",
    )
    assert purchase.total_amount == Decimal("50.00")
    assert purchase.invoice_number is not None
    assert purchase.payment_status == PaymentStatus.PENDING


async def test_purchase_inactive_seed_rejected(db_session, active_farmer, super_admin):
    seed = await purchase_service.create_seed(
        db_session, admin=super_admin, name="Retired Seed", variety=None,
        price_per_kg=Decimal("10.00"), stock_kg=Decimal("50"), description=None, image_url=None,
    )
    await db_session.commit()
    await purchase_service.delete_seed(db_session, admin=super_admin, seed_id=seed.id)
    await db_session.commit()

    with pytest.raises(ConflictError):
        await purchase_service.purchase_seeds(
            db_session, farmer=active_farmer, seed_id=seed.id, quantity_kg=Decimal("1"),
            warehouse_id=None, payment_method=None, upi_id=None,
        )


async def test_update_purchase_status_to_paid_completes_transaction(db_session, active_farmer, super_admin):
    from sqlalchemy import select

    from app.models.enums import TransactionStatus
    from app.models.ledger import Transaction

    seed = await purchase_service.create_seed(
        db_session, admin=super_admin, name="Pay Seed", variety=None,
        price_per_kg=Decimal("20.00"), stock_kg=Decimal("50"), description=None, image_url=None,
    )
    await db_session.commit()
    purchase = await purchase_service.purchase_seeds(
        db_session, farmer=active_farmer, seed_id=seed.id, quantity_kg=Decimal("2"),
        warehouse_id=None, payment_method=None, upi_id=None,
    )
    await db_session.commit()

    await purchase_service.update_purchase_status(
        db_session, admin=super_admin, purchase_id=purchase.id, new_status=PaymentStatus.PAID
    )
    await db_session.commit()

    result = await db_session.execute(select(Transaction).where(Transaction.reference_id == purchase.id))
    txn = result.scalar_one()
    assert txn.status == TransactionStatus.COMPLETED


async def test_update_status_on_missing_purchase_raises_not_found(db_session, super_admin):
    import uuid

    with pytest.raises(NotFoundError):
        await purchase_service.update_purchase_status(
            db_session, admin=super_admin, purchase_id=uuid.uuid4(), new_status=PaymentStatus.PAID
        )
