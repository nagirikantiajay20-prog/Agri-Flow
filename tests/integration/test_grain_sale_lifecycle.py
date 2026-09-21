from datetime import date
from decimal import Decimal

import pytest

from app.core.exceptions import InvalidStateTransitionError
from app.models.enums import BookingStatus, GrainGrade
from app.services import grain_sale_service, market_rate_service

pytestmark = pytest.mark.asyncio


async def _make_booking(db_session, farmer):
    from app.models.warehouse import BookingSlot, Warehouse

    warehouse = Warehouse(name="Grain Test WH", address="Addr", total_capacity_kg=Decimal("1000"))
    db_session.add(warehouse)
    await db_session.flush()
    booking = BookingSlot(
        farmer_id=farmer.id, warehouse_id=warehouse.id, booking_date=date.today(),
        delivery_address="addr", grain_type="Rice", quantity_kg=Decimal("100"), status=BookingStatus.CONFIRMED,
    )
    db_session.add(booking)
    await db_session.flush()
    return booking


async def test_full_grain_sale_lifecycle_with_pinned_price(db_session, active_farmer, super_admin):
    crop_type = "TestRiceForLifecycle"
    await market_rate_service.set_rate(
        db_session, admin=super_admin, crop_type=crop_type, grade=GrainGrade.A,
        price_per_kg=Decimal("18.00"), effective_date=date(2020, 1, 1),
    )
    await db_session.commit()

    sale = await grain_sale_service.create_grain_sale(
        db_session, farmer=active_farmer, grain_type=crop_type, grade=GrainGrade.A,
        raw_material_kg=Decimal("100"), crop_id=None,
    )
    await db_session.commit()
    assert sale.status.value == "pending"

    booking = await _make_booking(db_session, active_farmer)
    await db_session.commit()

    reviewed = await grain_sale_service.review_grain_sale(
        db_session, manager=super_admin, sale_id=sale.id, booking_slot_id=booking.id,
        good_quantity_kg=Decimal("90"), bad_quantity_kg=Decimal("10"), rejection_reason=None, approve=True,
    )
    await db_session.commit()
    assert reviewed.status.value == "approved"
    assert reviewed.price_per_kg == Decimal("18.00")
    assert reviewed.total_amount == Decimal("1620.00")  # 90 * 18.00

    # Rate changes AFTER approval must not retroactively change the sale.
    await market_rate_service.set_rate(
        db_session, admin=super_admin, crop_type=crop_type, grade=GrainGrade.A,
        price_per_kg=Decimal("99.00"), effective_date=date.today(),
    )
    await db_session.commit()

    paid = await grain_sale_service.pay_grain_sale(
        db_session, manager=super_admin, sale_id=sale.id, payment_reference="TXN123"
    )
    assert paid.status.value == "paid"
    assert paid.total_amount == Decimal("1620.00"), "Price must stay pinned at approval time, not re-derived at payment"


async def test_rejected_grain_sale_cannot_be_paid(db_session, active_farmer, super_admin):
    sale = await grain_sale_service.create_grain_sale(
        db_session, farmer=active_farmer, grain_type="Wheat", grade=GrainGrade.B,
        raw_material_kg=Decimal("50"), crop_id=None,
    )
    await db_session.commit()
    booking = await _make_booking(db_session, active_farmer)
    await db_session.commit()

    await grain_sale_service.review_grain_sale(
        db_session, manager=super_admin, sale_id=sale.id, booking_slot_id=booking.id,
        good_quantity_kg=Decimal("0"), bad_quantity_kg=Decimal("50"),
        rejection_reason="Too much moisture content", approve=False,
    )
    await db_session.commit()

    with pytest.raises(InvalidStateTransitionError):
        await grain_sale_service.pay_grain_sale(db_session, manager=super_admin, sale_id=sale.id, payment_reference=None)
