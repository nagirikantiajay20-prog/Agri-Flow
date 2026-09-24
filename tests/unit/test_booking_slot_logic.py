"""
Comprehensive isolated unit tests for Restored Explicit Warehouse Slot Booking Architecture.
Enforces real warehouse_slot requirement, explicit slot selection, capacity checks, and zero DB dependencies.
"""
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import (
    CapacityExceededError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.models.enums import BookingStatus, UserRole, UserStatus
from app.models.user import User
from app.models.warehouse import BookingSlot, Warehouse, WarehouseSlot
from app.services import booking_service
from app.services.booking_service import BOOKABLE_SLOT_STATUSES

pytestmark = pytest.mark.asyncio


def _make_user(role=UserRole.FARMER, name="Ramesh Farmer") -> User:
    return User(
        id=uuid.uuid4(),
        phone="9502662924",
        name=name,
        role=role,
        status=UserStatus.ACTIVE,
    )


def _make_warehouse(
    wh_id: uuid.UUID | None = None,
    total_kg: Decimal = Decimal("500000"),
    current_kg: Decimal = Decimal("100000"),
    is_active: bool = True,
) -> Warehouse:
    return Warehouse(
        id=wh_id or uuid.uuid4(),
        name="Central Warehouse A",
        address="Kalluru Hub",
        total_capacity_kg=total_kg,
        current_load_kg=current_kg,
        is_active=is_active,
    )


def _make_slot(
    slot_id: uuid.UUID | None = None,
    wh_id: uuid.UUID | None = None,
    slot_date: date | None = None,
    capacity_kg: Decimal = Decimal("10000"),
    booked_kg: Decimal = Decimal("2000"),
    max_bookings: int = 10,
    current_count: int = 2,
    status: str = "active",
) -> WarehouseSlot:
    return WarehouseSlot(
        id=slot_id or uuid.uuid4(),
        warehouse_id=wh_id or uuid.uuid4(),
        slot_date=slot_date or date.today() + timedelta(days=1),
        start_time=time(9, 0),
        end_time=time(12, 0),
        capacity_kg=capacity_kg,
        booked_kg=booked_kg,
        max_bookings=max_bookings,
        current_booking_count=current_count,
        status=status,
    )


# 1. Active warehouse + capacity + explicit slot -> booking succeeds and links warehouse_slot_id
@patch("app.services.notification_service.notify_roles", new_callable=AsyncMock)
@patch("app.services.notification_service.notify_user", new_callable=AsyncMock)
@patch("app.services.audit_service.record", new_callable=AsyncMock)
async def test_explicit_slot_booking_succeeds(mock_audit, mock_notify_user, mock_notify_roles):
    wh_id = uuid.uuid4()
    slot_id = uuid.uuid4()
    target_date = date.today() + timedelta(days=2)
    farmer = _make_user()
    wh = _make_warehouse(wh_id=wh_id, total_kg=Decimal("500000"), current_kg=Decimal("100000"))
    slot = _make_slot(
        slot_id=slot_id,
        wh_id=wh_id,
        slot_date=target_date,
        capacity_kg=Decimal("10000"),
        booked_kg=Decimal("2000"),
        status="active",
    )

    db = AsyncMock()
    wh_res = MagicMock()
    wh_res.scalar_one_or_none.return_value = wh

    slot_res = MagicMock()
    slot_res.scalar_one_or_none.return_value = slot

    dup_res = MagicMock()
    dup_res.scalar_one_or_none.return_value = None

    db.execute.side_effect = [wh_res, slot_res, dup_res]

    booking = await booking_service.create_booking(
        db,
        farmer=farmer,
        warehouse_id=wh_id,
        warehouse_slot_id=slot_id,
        booking_date=target_date,
        delivery_address="Plot 1",
        grain_type="Cotton (Kapus)",
        quantity_kg=Decimal("1500"),
        grain_sale_id=None,
        notes=None,
    )

    assert booking.warehouse_slot_id == slot_id
    assert booking.status == BookingStatus.PENDING
    assert slot.booked_kg == Decimal("3500")
    assert slot.current_booking_count == 3
    assert wh.current_load_kg == Decimal("101500")


# 2. Open status slot (historical Central Warehouse A) -> booking succeeds
@patch("app.services.notification_service.notify_roles", new_callable=AsyncMock)
@patch("app.services.notification_service.notify_user", new_callable=AsyncMock)
@patch("app.services.audit_service.record", new_callable=AsyncMock)
async def test_open_status_slot_succeeds(mock_audit, mock_notify_user, mock_notify_roles):
    wh_id = uuid.uuid4()
    slot_id = uuid.uuid4()
    target_date = date.today() + timedelta(days=1)
    farmer = _make_user()
    wh = _make_warehouse(wh_id=wh_id)
    slot = _make_slot(
        slot_id=slot_id,
        wh_id=wh_id,
        slot_date=target_date,
        capacity_kg=Decimal("8000"),
        booked_kg=Decimal("1000"),
        status="open",
    )

    db = AsyncMock()
    wh_res = MagicMock()
    wh_res.scalar_one_or_none.return_value = wh
    slot_res = MagicMock()
    slot_res.scalar_one_or_none.return_value = slot
    dup_res = MagicMock()
    dup_res.scalar_one_or_none.return_value = None
    db.execute.side_effect = [wh_res, slot_res, dup_res]

    booking = await booking_service.create_booking(
        db,
        farmer=farmer,
        warehouse_id=wh_id,
        warehouse_slot_id=slot_id,
        booking_date=target_date,
        delivery_address="Plot 2",
        grain_type="Paddy",
        quantity_kg=Decimal("2000"),
        grain_sale_id=None,
        notes=None,
    )

    assert booking.warehouse_slot_id == slot_id
    assert slot.booked_kg == Decimal("3000")


# 3. Active warehouse + capacity + NO slots for date -> rejected with clean NotFoundError
async def test_no_slots_scheduled_returns_not_found():
    wh_id = uuid.uuid4()
    target_date = date.today() + timedelta(days=1)
    farmer = _make_user()
    wh = _make_warehouse(wh_id=wh_id, total_kg=Decimal("200000"), current_kg=Decimal("50000"))

    db = AsyncMock()
    wh_res = MagicMock()
    wh_res.scalar_one_or_none.return_value = wh

    slot_res = MagicMock()
    slot_res.scalars.return_value.all.return_value = []
    db.execute.side_effect = [wh_res, slot_res]

    with pytest.raises(NotFoundError) as exc_info:
        await booking_service.create_booking(
            db,
            farmer=farmer,
            warehouse_id=wh_id,
            warehouse_slot_id=None,
            booking_date=target_date,
            delivery_address="Plot 3",
            grain_type="Wheat",
            quantity_kg=Decimal("1000"),
            grain_sale_id=None,
            notes=None,
        )

    assert "no delivery slots are available" in str(exc_info.value).lower()


# 4. Inactive warehouse -> rejected
async def test_inactive_warehouse_rejected():
    wh_id = uuid.uuid4()
    farmer = _make_user()
    wh = _make_warehouse(wh_id=wh_id, is_active=False)

    db = AsyncMock()
    wh_res = MagicMock()
    wh_res.scalar_one_or_none.return_value = wh
    db.execute.return_value = wh_res

    with pytest.raises(ConflictError) as exc_info:
        await booking_service.create_booking(
            db,
            farmer=farmer,
            warehouse_id=wh_id,
            warehouse_slot_id=None,
            booking_date=date.today() + timedelta(days=1),
            delivery_address="Farm",
            grain_type="Wheat",
            quantity_kg=Decimal("1000"),
            grain_sale_id=None,
            notes=None,
        )

    assert "inactive" in str(exc_info.value).lower()


# 5. Warehouse physical capacity exceeded -> rejected
async def test_warehouse_capacity_exceeded_rejected():
    wh_id = uuid.uuid4()
    farmer = _make_user()
    wh = _make_warehouse(wh_id=wh_id, total_kg=Decimal("10000"), current_kg=Decimal("9500"))  # only 500 kg left

    db = AsyncMock()
    wh_res = MagicMock()
    wh_res.scalar_one_or_none.return_value = wh
    db.execute.return_value = wh_res

    with pytest.raises(CapacityExceededError) as exc_info:
        await booking_service.create_booking(
            db,
            farmer=farmer,
            warehouse_id=wh_id,
            warehouse_slot_id=None,
            booking_date=date.today() + timedelta(days=1),
            delivery_address="Farm",
            grain_type="Wheat",
            quantity_kg=Decimal("800"),  # 800 > 500
            grain_sale_id=None,
            notes=None,
        )

    assert "capacity exceeded" in str(exc_info.value).lower()


# 6. Explicit slot capacity exceeded -> rejected
async def test_slot_capacity_exceeded_rejected():
    wh_id = uuid.uuid4()
    slot_id = uuid.uuid4()
    target_date = date.today() + timedelta(days=1)
    farmer = _make_user()
    wh = _make_warehouse(wh_id=wh_id, total_kg=Decimal("500000"), current_kg=Decimal("10000"))
    slot = _make_slot(
        slot_id=slot_id,
        wh_id=wh_id,
        slot_date=target_date,
        capacity_kg=Decimal("5000"),
        booked_kg=Decimal("4500"),  # only 500 kg remaining
    )

    db = AsyncMock()
    wh_res = MagicMock()
    wh_res.scalar_one_or_none.return_value = wh
    slot_res = MagicMock()
    slot_res.scalar_one_or_none.return_value = slot
    db.execute.side_effect = [wh_res, slot_res]

    with pytest.raises(CapacityExceededError) as exc_info:
        await booking_service.create_booking(
            db,
            farmer=farmer,
            warehouse_id=wh_id,
            warehouse_slot_id=slot_id,
            booking_date=target_date,
            delivery_address="Farm",
            grain_type="Cotton",
            quantity_kg=Decimal("600"),  # 600 > 500
            grain_sale_id=None,
            notes=None,
        )

    assert "remaining in this slot" in str(exc_info.value)


# 7. Slot booking limit reached -> rejected
async def test_slot_max_bookings_limit_reached():
    wh_id = uuid.uuid4()
    slot_id = uuid.uuid4()
    target_date = date.today() + timedelta(days=1)
    farmer = _make_user()
    wh = _make_warehouse(wh_id=wh_id)
    slot = _make_slot(
        slot_id=slot_id,
        wh_id=wh_id,
        slot_date=target_date,
        capacity_kg=Decimal("10000"),
        booked_kg=Decimal("1000"),
        max_bookings=5,
        current_count=5,  # reached limit
    )

    db = AsyncMock()
    wh_res = MagicMock()
    wh_res.scalar_one_or_none.return_value = wh
    slot_res = MagicMock()
    slot_res.scalar_one_or_none.return_value = slot
    db.execute.side_effect = [wh_res, slot_res]

    with pytest.raises(CapacityExceededError) as exc_info:
        await booking_service.create_booking(
            db,
            farmer=farmer,
            warehouse_id=wh_id,
            warehouse_slot_id=slot_id,
            booking_date=target_date,
            delivery_address="Farm",
            grain_type="Cotton",
            quantity_kg=Decimal("500"),
            grain_sale_id=None,
            notes=None,
        )

    assert "reached its booking limit" in str(exc_info.value)


# 8. Duplicate active farmer booking on same slot -> rejected
async def test_duplicate_active_farmer_booking_rejected():
    wh_id = uuid.uuid4()
    slot_id = uuid.uuid4()
    target_date = date.today() + timedelta(days=1)
    farmer = _make_user()
    wh = _make_warehouse(wh_id=wh_id)
    slot = _make_slot(slot_id=slot_id, wh_id=wh_id, slot_date=target_date)

    existing_booking = BookingSlot(
        id=uuid.uuid4(),
        farmer_id=farmer.id,
        warehouse_slot_id=slot_id,
        warehouse_id=wh_id,
        booking_date=target_date,
        status=BookingStatus.PENDING,
    )

    db = AsyncMock()
    wh_res = MagicMock()
    wh_res.scalar_one_or_none.return_value = wh
    slot_res = MagicMock()
    slot_res.scalar_one_or_none.return_value = slot
    dup_res = MagicMock()
    dup_res.scalar_one_or_none.return_value = existing_booking
    db.execute.side_effect = [wh_res, slot_res, dup_res]

    with pytest.raises(ConflictError) as exc_info:
        await booking_service.create_booking(
            db,
            farmer=farmer,
            warehouse_id=wh_id,
            warehouse_slot_id=slot_id,
            booking_date=target_date,
            delivery_address="Farm",
            grain_type="Cotton",
            quantity_kg=Decimal("500"),
            grain_sale_id=None,
            notes=None,
        )

    assert "already have an active booking" in str(exc_info.value)


# 9. Past date -> rejected
async def test_past_date_rejected():
    wh_id = uuid.uuid4()
    farmer = _make_user()
    db = AsyncMock()

    past_date = date.today() - timedelta(days=1)
    with pytest.raises(ValidationError) as exc_info:
        await booking_service.create_booking(
            db,
            farmer=farmer,
            warehouse_id=wh_id,
            warehouse_slot_id=None,
            booking_date=past_date,
            delivery_address="Farm",
            grain_type="Cotton",
            quantity_kg=Decimal("500"),
            grain_sale_id=None,
            notes=None,
        )

    assert "past" in str(exc_info.value).lower()


# 10. Mismatched warehouse or slot date -> rejected
async def test_mismatched_warehouse_or_date_rejected():
    wh_id = uuid.uuid4()
    other_wh_id = uuid.uuid4()
    slot_id = uuid.uuid4()
    farmer = _make_user()
    wh = _make_warehouse(wh_id=wh_id)
    slot_for_other_wh = _make_slot(slot_id=slot_id, wh_id=other_wh_id)

    db = AsyncMock()
    wh_res = MagicMock()
    wh_res.scalar_one_or_none.return_value = wh
    slot_res = MagicMock()
    slot_res.scalar_one_or_none.return_value = slot_for_other_wh
    db.execute.side_effect = [wh_res, slot_res]

    with pytest.raises(NotFoundError):
        await booking_service.create_booking(
            db,
            farmer=farmer,
            warehouse_id=wh_id,
            warehouse_slot_id=slot_id,
            booking_date=slot_for_other_wh.slot_date,
            delivery_address="Plot",
            grain_type="Cotton",
            quantity_kg=Decimal("100"),
            grain_sale_id=None,
            notes=None,
        )


# 11. Row-level locking enforced on warehouse and slot
async def test_row_level_locking_enforced():
    wh_id = uuid.uuid4()
    slot_id = uuid.uuid4()
    target_date = date.today() + timedelta(days=1)
    farmer = _make_user()
    wh = _make_warehouse(wh_id=wh_id)
    slot = _make_slot(slot_id=slot_id, wh_id=wh_id, slot_date=target_date)

    db = AsyncMock()
    wh_res = MagicMock()
    wh_res.scalar_one_or_none.return_value = wh
    slot_res = MagicMock()
    slot_res.scalar_one_or_none.return_value = slot
    dup_res = MagicMock()
    dup_res.scalar_one_or_none.return_value = None
    db.execute.side_effect = [wh_res, slot_res, dup_res]

    with patch("app.services.notification_service.notify_roles", new_callable=AsyncMock), \
         patch("app.services.notification_service.notify_user", new_callable=AsyncMock), \
         patch("app.services.audit_service.record", new_callable=AsyncMock):
        await booking_service.create_booking(
            db,
            farmer=farmer,
            warehouse_id=wh_id,
            warehouse_slot_id=slot_id,
            booking_date=target_date,
            delivery_address="Farm",
            grain_type="Wheat",
            quantity_kg=Decimal("100"),
            grain_sale_id=None,
            notes=None,
        )

    first_call_stmt = db.execute.call_args_list[0][0][0]
    compiled_str = str(first_call_stmt)
    assert "FOR UPDATE" in compiled_str or "with_for_update" in repr(first_call_stmt).lower()


# 12. Cancellation releases capacity on both slot and warehouse
@patch("app.services.notification_service.notify_user", new_callable=AsyncMock)
@patch("app.services.audit_service.record", new_callable=AsyncMock)
async def test_cancellation_releases_capacity(mock_audit, mock_notify_user):
    wh_id = uuid.uuid4()
    slot_id = uuid.uuid4()
    actor = _make_user(role=UserRole.MANAGER)
    wh = _make_warehouse(wh_id=wh_id, current_kg=Decimal("50000"))
    slot = _make_slot(slot_id=slot_id, wh_id=wh_id, booked_kg=Decimal("10000"), current_count=3)

    booking_with_slot = BookingSlot(
        id=uuid.uuid4(),
        farmer_id=uuid.uuid4(),
        warehouse_id=wh_id,
        warehouse_slot_id=slot_id,
        quantity_kg=Decimal("2000"),
        status=BookingStatus.PENDING,
    )

    db = AsyncMock()
    b_res = MagicMock()
    b_res.scalar_one_or_none.return_value = booking_with_slot
    slot_res = MagicMock()
    slot_res.scalar_one_or_none.return_value = slot
    wh_res = MagicMock()
    wh_res.scalar_one_or_none.return_value = wh

    db.execute.side_effect = [b_res, slot_res, wh_res]

    cancelled = await booking_service.update_booking_status(
        db, actor=actor, booking_id=booking_with_slot.id, new_status=BookingStatus.CANCELLED, notes=None
    )
    assert cancelled.status == BookingStatus.CANCELLED
    assert slot.booked_kg == Decimal("8000")
    assert slot.current_booking_count == 2
    assert wh.current_load_kg == Decimal("48000")


# 13. slot_time presentation returns None if slot is None, formatted if slot is present
async def test_slot_time_mapper():
    from app.api.v1.farmer._mappers import slot_time

    assert slot_time(None) is None
    sample_slot = _make_slot()
    sample_slot.start_time = time(9, 30)
    sample_slot.end_time = time(12, 45)
    assert slot_time(sample_slot) == "09:30 AM - 12:45 PM"
