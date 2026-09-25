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


async def test_grade_pricing_and_fallback(db_session, active_farmer, super_admin):
    from app.core.exceptions import ValidationError

    # Seed with all grades configured
    seed = await purchase_service.create_seed(
        db_session,
        admin=super_admin,
        name="Multi-Grade Wheat",
        variety="W-1",
        price_per_kg=Decimal("100.00"),
        price_grade_a=Decimal("120.00"),
        price_grade_b=Decimal("110.00"),
        price_grade_c=Decimal("90.00"),
        max_order_quantity_kg=Decimal("50.00"),
        stock_kg=Decimal("1000"),
    )
    await db_session.commit()

    # 1. Purchase Grade A
    p_a = await purchase_service.purchase_seeds(
        db_session, farmer=active_farmer, seed_id=seed.id, quantity_kg=Decimal("2"),
        warehouse_id=None, payment_method=None, upi_id=None, grade="A",
    )
    assert p_a.price_per_kg == Decimal("120.00")
    assert p_a.total_amount == Decimal("240.00")
    assert p_a.grade == "A"

    # 2. Purchase Grade B
    p_b = await purchase_service.purchase_seeds(
        db_session, farmer=active_farmer, seed_id=seed.id, quantity_kg=Decimal("2"),
        warehouse_id=None, payment_method=None, upi_id=None, grade="B",
    )
    assert p_b.price_per_kg == Decimal("110.00")
    assert p_b.total_amount == Decimal("220.00")
    assert p_b.grade == "B"

    # 3. Purchase Grade C
    p_c = await purchase_service.purchase_seeds(
        db_session, farmer=active_farmer, seed_id=seed.id, quantity_kg=Decimal("2"),
        warehouse_id=None, payment_method=None, upi_id=None, grade="C",
    )
    assert p_c.price_per_kg == Decimal("90.00")
    assert p_c.total_amount == Decimal("180.00")
    assert p_c.grade == "C"

    # 4. Purchase without grade -> fallback to base price_per_kg
    p_none = await purchase_service.purchase_seeds(
        db_session, farmer=active_farmer, seed_id=seed.id, quantity_kg=Decimal("2"),
        warehouse_id=None, payment_method=None, upi_id=None, grade=None,
    )
    assert p_none.price_per_kg == Decimal("100.00")
    assert p_none.total_amount == Decimal("200.00")

    # 5. Invalid grade -> rejected with ValidationError
    with pytest.raises(ValidationError):
        await purchase_service.purchase_seeds(
            db_session, farmer=active_farmer, seed_id=seed.id, quantity_kg=Decimal("2"),
            warehouse_id=None, payment_method=None, upi_id=None, grade="X",
        )


async def test_grade_pricing_fallback_when_grade_price_is_null(db_session, active_farmer, super_admin):
    # Seed with base price only (all grade prices NULL)
    seed = await purchase_service.create_seed(
        db_session,
        admin=super_admin,
        name="Standard Mustard",
        variety="M-1",
        price_per_kg=Decimal("75.00"),
        price_grade_a=None,
        price_grade_b=None,
        price_grade_c=None,
        stock_kg=Decimal("500"),
    )
    await db_session.commit()

    # Grade A requested on seed without price_grade_a -> falls back to base 75.00
    p = await purchase_service.purchase_seeds(
        db_session, farmer=active_farmer, seed_id=seed.id, quantity_kg=Decimal("10"),
        warehouse_id=None, payment_method=None, upi_id=None, grade="A",
    )
    assert p.price_per_kg == Decimal("75.00")
    assert p.total_amount == Decimal("750.00")
    assert p.grade == "A"


async def test_max_order_quantity_limit(db_session, active_farmer, super_admin):
    from app.core.exceptions import ValidationError

    seed = await purchase_service.create_seed(
        db_session,
        admin=super_admin,
        name="Capped Seed",
        variety="C-1",
        price_per_kg=Decimal("50.00"),
        max_order_quantity_kg=Decimal("30.00"),
        stock_kg=Decimal("500"),
    )
    await db_session.commit()

    # Below limit -> success
    p_below = await purchase_service.purchase_seeds(
        db_session, farmer=active_farmer, seed_id=seed.id, quantity_kg=Decimal("20"),
        warehouse_id=None, payment_method=None, upi_id=None,
    )
    assert p_below.quantity_kg == Decimal("20")

    # Equal to limit -> success
    p_equal = await purchase_service.purchase_seeds(
        db_session, farmer=active_farmer, seed_id=seed.id, quantity_kg=Decimal("30"),
        warehouse_id=None, payment_method=None, upi_id=None,
    )
    assert p_equal.quantity_kg == Decimal("30")

    # Above limit -> raises ValidationError
    with pytest.raises(ValidationError):
        await purchase_service.purchase_seeds(
            db_session, farmer=active_farmer, seed_id=seed.id, quantity_kg=Decimal("30.01"),
            warehouse_id=None, payment_method=None, upi_id=None,
        )
