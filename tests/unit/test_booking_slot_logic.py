"""
Comprehensive unit tests for Option 2: Automatic Warehouse Booking Architecture.
Tests scenarios A through N in total isolation with zero DB or external dependencies.
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
from app.services.booking_service import (
    BOOKABLE_SLOT_STATUSES,
    DEFAULT_OPERATING_END,
    DEFAULT_OPERATING_START,
    DEFAULT_OPERATING_WINDOW,
)

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


# Scenario A: Active warehouse + capacity + explicit slot -> booking succeeds.
@patch("app.services.notification_service.notify_roles", new_callable=AsyncMock)
@patch("app.services.notification_service.notify_user", new_callable=AsyncMock)
@patch("app.services.audit_service.record", new_callable=AsyncMock)
async def test_scenario_a_active_warehouse_explicit_slot_succeeds(mock_audit, mock_notify_user, mock_notify_roles):
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


# Scenario B: Active warehouse + capacity + NO explicit slot -> automatic booking succeeds.
@patch("app.services.notification_service.notify_roles", new_callable=AsyncMock)
@patch("app.services.notification_service.notify_user", new_callable=AsyncMock)
@patch("app.services.audit_service.record", new_callable=AsyncMock)
async def test_scenario_b_active_warehouse_no_slot_automatic_booking_succeeds(mock_audit, mock_notify_user, mock_notify_roles):
    wh_id = uuid.uuid4()
    target_date = date.today() + timedelta(days=1)
    farmer = _make_user()
    wh = _make_warehouse(wh_id=wh_id, total_kg=Decimal("200000"), current_kg=Decimal("50000"))

    db = AsyncMock()
    wh_res = MagicMock()
    wh_res.scalar_one_or_none.return_value = wh

    # Query for explicit slots returns empty list []
    slot_res = MagicMock()
    slot_res.scalars.return_value.all.return_value = []

    # Duplicate check returns None
    dup_res = MagicMock()
    dup_res.scalar_one_or_none.return_value = None

    db.execute.side_effect = [wh_res, slot_res, dup_res]

    booking = await booking_service.create_booking(
        db,
        farmer=farmer,
        warehouse_id=wh_id,
        warehouse_slot_id=None,
        booking_date=target_date,
        delivery_address="Plot 2",
        grain_type="Paddy / Rice",
        quantity_kg=Decimal("3000"),
        grain_sale_id=None,
        notes=None,
    )

    assert booking.warehouse_slot_id is None
    assert booking.warehouse_id == wh_id
    assert booking.quantity_kg == Decimal("3000")
    assert booking.status == BookingStatus.PENDING
    assert DEFAULT_OPERATING_WINDOW in booking.notes
    assert wh.current_load_kg == Decimal("53000")


# Scenario C: Inactive warehouse -> rejected.
async def test_scenario_c_inactive_warehouse_rejected():
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


# Scenario D: Zero/insufficient physical capacity -> rejected.
async def test_scenario_d_insufficient_physical_capacity_rejected():
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


# Scenario E: Explicit slot capacity insufficient -> rejected.
async def test_scenario_e_explicit_slot_capacity_insufficient_rejected():
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
        booked_kg=Decimal("4500"),  # 500 kg left in slot
        status="active",
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


# Scenario F: Automatic booking capacity insufficient -> rejected.
async def test_scenario_f_automatic_booking_capacity_insufficient_rejected():
    wh_id = uuid.uuid4()
    farmer = _make_user()
    wh = _make_warehouse(wh_id=wh_id, total_kg=Decimal("1000"), current_kg=Decimal("1000"))  # 0 kg left

    db = AsyncMock()
    wh_res = MagicMock()
    wh_res.scalar_one_or_none.return_value = wh
    db.execute.return_value = wh_res

    with pytest.raises(CapacityExceededError):
        await booking_service.create_booking(
            db,
            farmer=farmer,
            warehouse_id=wh_id,
            warehouse_slot_id=None,
            booking_date=date.today() + timedelta(days=1),
            delivery_address="Farm",
            grain_type="Cotton",
            quantity_kg=Decimal("100"),
            grain_sale_id=None,
            notes=None,
        )


# Scenario G: Duplicate active farmer booking -> rejected.
async def test_scenario_g_duplicate_active_farmer_booking_rejected():
    wh_id = uuid.uuid4()
    target_date = date.today() + timedelta(days=1)
    farmer = _make_user()
    wh = _make_warehouse(wh_id=wh_id)

    existing_booking = BookingSlot(
        id=uuid.uuid4(),
        farmer_id=farmer.id,
        warehouse_id=wh_id,
        booking_date=target_date,
        status=BookingStatus.PENDING,
    )

    db = AsyncMock()
    wh_res = MagicMock()
    wh_res.scalar_one_or_none.return_value = wh

    slot_res = MagicMock()
    slot_res.scalars.return_value.all.return_value = []

    dup_res = MagicMock()
    dup_res.scalar_one_or_none.return_value = existing_booking

    db.execute.side_effect = [wh_res, slot_res, dup_res]

    with pytest.raises(ConflictError) as exc_info:
        await booking_service.create_booking(
            db,
            farmer=farmer,
            warehouse_id=wh_id,
            warehouse_slot_id=None,
            booking_date=target_date,
            delivery_address="Farm",
            grain_type="Cotton",
            quantity_kg=Decimal("500"),
            grain_sale_id=None,
            notes=None,
        )

    assert "already have an active booking" in str(exc_info.value)


# Scenario H: Explicit slot has priority over automatic mode.
@patch("app.services.notification_service.notify_roles", new_callable=AsyncMock)
@patch("app.services.notification_service.notify_user", new_callable=AsyncMock)
@patch("app.services.audit_service.record", new_callable=AsyncMock)
async def test_scenario_h_explicit_slot_priority_over_automatic(mock_audit, mock_notify_user, mock_notify_roles):
    wh_id = uuid.uuid4()
    slot_id = uuid.uuid4()
    target_date = date.today() + timedelta(days=2)
    farmer = _make_user()
    wh = _make_warehouse(wh_id=wh_id)
    explicit_slot = _make_slot(
        slot_id=slot_id,
        wh_id=wh_id,
        slot_date=target_date,
        capacity_kg=Decimal("10000"),
        booked_kg=Decimal("0"),
        status="open",
    )

    db = AsyncMock()
    wh_res = MagicMock()
    wh_res.scalar_one_or_none.return_value = wh

    # Explicit slot exists on this date
    slot_res = MagicMock()
    slot_res.scalars.return_value.all.return_value = [explicit_slot]

    dup_res = MagicMock()
    dup_res.scalar_one_or_none.return_value = None

    db.execute.side_effect = [wh_res, slot_res, dup_res]

    # Even though warehouse_slot_id=None was sent, explicit slot is prioritized and linked
    booking = await booking_service.create_booking(
        db,
        farmer=farmer,
        warehouse_id=wh_id,
        warehouse_slot_id=None,
        booking_date=target_date,
        delivery_address="Plot 5",
        grain_type="Wheat",
        quantity_kg=Decimal("1200"),
        grain_sale_id=None,
        notes=None,
    )

    assert booking.warehouse_slot_id == slot_id
    assert explicit_slot.booked_kg == Decimal("1200")


# Scenario I: Past date -> rejected.
async def test_scenario_i_past_date_rejected():
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


# Scenario J: Warehouse/date changes refresh authoritative availability.
async def test_scenario_j_refresh_authoritative_availability():
    wh_id_1 = uuid.uuid4()
    wh_id_2 = uuid.uuid4()
    target_date = date.today() + timedelta(days=3)

    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = []
    db.execute.return_value = result_mock

    # Query warehouse 1
    await booking_service.list_slots_filtered(db, warehouse_id=wh_id_1, slot_date=target_date)
    stmt1 = str(db.execute.call_args[0][0])
    assert "warehouse_slots.warehouse_id" in stmt1

    # Query warehouse 2
    await booking_service.list_slots_filtered(db, warehouse_id=wh_id_2, slot_date=target_date)
    stmt2 = str(db.execute.call_args[0][0])
    assert "warehouse_slots.warehouse_id" in stmt2


# Scenario K: Concurrent bookings cannot overbook capacity.
async def test_scenario_k_row_level_locking_enforced():
    wh_id = uuid.uuid4()
    farmer = _make_user()
    wh = _make_warehouse(wh_id=wh_id)

    db = AsyncMock()
    wh_res = MagicMock()
    wh_res.scalar_one_or_none.return_value = wh

    slot_res = MagicMock()
    slot_res.scalars.return_value.all.return_value = []

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
            warehouse_slot_id=None,
            booking_date=date.today() + timedelta(days=1),
            delivery_address="Farm",
            grain_type="Wheat",
            quantity_kg=Decimal("100"),
            grain_sale_id=None,
            notes=None,
        )

    # Verify that the warehouse query was executed with row-level locking (FOR UPDATE)
    first_call_stmt = db.execute.call_args_list[0][0][0]
    compiled_str = str(first_call_stmt)
    assert "FOR UPDATE" in compiled_str or "with_for_update" in repr(first_call_stmt).lower()


# Scenario L: Cancellation releases capacity according to existing semantics.
@patch("app.services.notification_service.notify_user", new_callable=AsyncMock)
@patch("app.services.audit_service.record", new_callable=AsyncMock)
async def test_scenario_l_cancellation_releases_capacity(mock_audit, mock_notify_user):
    wh_id = uuid.uuid4()
    slot_id = uuid.uuid4()
    actor = _make_user(role=UserRole.MANAGER)
    wh = _make_warehouse(wh_id=wh_id, current_kg=Decimal("50000"))
    slot = _make_slot(slot_id=slot_id, wh_id=wh_id, booked_kg=Decimal("10000"), current_count=3)

    # 1. Booking with explicit slot
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
    assert slot.booked_kg == Decimal("8000")  # 10000 - 2000
    assert slot.current_booking_count == 2
    assert wh.current_load_kg == Decimal("48000")  # 50000 - 2000

    # 2. Automatic booking without slot (warehouse_slot_id=None)
    booking_auto = BookingSlot(
        id=uuid.uuid4(),
        farmer_id=uuid.uuid4(),
        warehouse_id=wh_id,
        warehouse_slot_id=None,
        quantity_kg=Decimal("3000"),
        status=BookingStatus.PENDING,
    )
    b_res2 = MagicMock()
    b_res2.scalar_one_or_none.return_value = booking_auto
    wh_res2 = MagicMock()
    wh_res2.scalar_one_or_none.return_value = wh

    db.execute.side_effect = [b_res2, wh_res2]
    cancelled_auto = await booking_service.update_booking_status(
        db, actor=actor, booking_id=booking_auto.id, new_status=BookingStatus.CANCELLED, notes=None
    )
    assert cancelled_auto.status == BookingStatus.CANCELLED
    assert wh.current_load_kg == Decimal("45000")  # 48000 - 3000


# Scenario M: Existing booking history remains correct.
async def test_scenario_m_booking_history_presentation():
    from app.api.v1.farmer._mappers import booking_out, slot_time

    # 1. slot_time with None returns standard operating window
    assert slot_time(None) == DEFAULT_OPERATING_WINDOW

    # 2. booking_out handles None warehouse_slot_id seamlessly
    wh = _make_warehouse()
    wh.contact_number = "9999900000"
    booking = BookingSlot(
        id=uuid.uuid4(),
        farmer_id=uuid.uuid4(),
        warehouse_id=wh.id,
        warehouse_slot_id=None,
        grain_type="Cotton (Kapus)",
        quantity_kg=Decimal("1500"),
        booking_date=date.today(),
        delivery_address="Kalluru Village",
        status=BookingStatus.PENDING,
        notes="Automatic drop-off",
        created_at=datetime.now(),
    )
    out = booking_out(booking, warehouse=wh, slot=None)
    assert out.warehouse_slot_id is None
    assert out.slot_time == DEFAULT_OPERATING_WINDOW
    assert out.status == BookingStatus.PENDING


# Scenario N: Existing notification flow still works exactly once.
@patch("app.services.notification_service.notify_roles", new_callable=AsyncMock)
@patch("app.services.notification_service.notify_user", new_callable=AsyncMock)
@patch("app.services.audit_service.record", new_callable=AsyncMock)
async def test_scenario_n_notification_pipeline_called_exactly_once(mock_audit, mock_notify_user, mock_notify_roles):
    wh_id = uuid.uuid4()
    farmer = _make_user()
    wh = _make_warehouse(wh_id=wh_id)

    db = AsyncMock()
    wh_res = MagicMock()
    wh_res.scalar_one_or_none.return_value = wh

    slot_res = MagicMock()
    slot_res.scalars.return_value.all.return_value = []

    dup_res = MagicMock()
    dup_res.scalar_one_or_none.return_value = None

    db.execute.side_effect = [wh_res, slot_res, dup_res]

    await booking_service.create_booking(
        db,
        farmer=farmer,
        warehouse_id=wh_id,
        warehouse_slot_id=None,
        booking_date=date.today() + timedelta(days=1),
        delivery_address="Farm",
        grain_type="Wheat",
        quantity_kg=Decimal("500"),
        grain_sale_id=None,
        notes=None,
    )

    assert mock_notify_user.call_count == 1
    assert mock_notify_roles.call_count == 1
    assert mock_audit.call_count == 1
