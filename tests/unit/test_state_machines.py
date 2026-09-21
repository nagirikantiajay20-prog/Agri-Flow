"""
State-machine tests — Master Plan §23: reject invalid transitions.
"""
from datetime import date
from decimal import Decimal

import pytest

from app.core.exceptions import InvalidStateTransitionError
from app.models.enums import GrainGrade
from app.services import grain_sale_service

pytestmark = pytest.mark.asyncio


async def test_grain_sale_cannot_skip_approval_to_paid(db_session, active_farmer, super_admin):
    sale = await grain_sale_service.create_grain_sale(
        db_session, farmer=active_farmer, grain_type="Rice", grade=GrainGrade.A,
        raw_material_kg=Decimal("100"), crop_id=None,
    )
    await db_session.commit()

    with pytest.raises(InvalidStateTransitionError):
        await grain_sale_service.pay_grain_sale(
            db_session, manager=super_admin, sale_id=sale.id, payment_reference=None
        )


async def test_grain_sale_cannot_be_reviewed_twice(db_session, active_farmer, super_admin):
    from app.models.enums import BookingStatus
    from app.models.warehouse import BookingSlot, Warehouse

    sale = await grain_sale_service.create_grain_sale(
        db_session, farmer=active_farmer, grain_type="Rice", grade=GrainGrade.A,
        raw_material_kg=Decimal("100"), crop_id=None,
    )
    warehouse = Warehouse(name="W", address="A", total_capacity_kg=Decimal("1000"))
    db_session.add(warehouse)
    await db_session.flush()
    booking = BookingSlot(
        farmer_id=active_farmer.id, warehouse_id=warehouse.id, booking_date=date.today(),
        delivery_address="addr", grain_type="Rice", quantity_kg=Decimal("100"), status=BookingStatus.CONFIRMED,
    )
    db_session.add(booking)
    await db_session.flush()

    await grain_sale_service.review_grain_sale(
        db_session, manager=super_admin, sale_id=sale.id, booking_slot_id=booking.id,
        good_quantity_kg=Decimal("95"), bad_quantity_kg=Decimal("5"), rejection_reason=None, approve=True,
    )
    await db_session.commit()

    with pytest.raises(InvalidStateTransitionError):
        await grain_sale_service.review_grain_sale(
            db_session, manager=super_admin, sale_id=sale.id, booking_slot_id=booking.id,
            good_quantity_kg=Decimal("95"), bad_quantity_kg=Decimal("5"), rejection_reason=None, approve=True,
        )
