"""
Comprehensive unit tests for Direct Warehouse Booking Architecture & Weather Service Caching.
Verifies warehouse physical capacity locking, direct date booking (warehouse_slot_id=None),
cancellation capacity release, and Redis weather fallback.
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
from app.services import booking_service, weather_service

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


# 1. Direct Warehouse Booking succeeds without requiring warehouse_slots
@patch("app.services.notification_service.notify_roles", new_callable=AsyncMock)
@patch("app.services.notification_service.notify_user", new_callable=AsyncMock)
@patch("app.services.audit_service.record", new_callable=AsyncMock)
async def test_direct_warehouse_booking_succeeds_without_slot(mock_audit, mock_notify_user, mock_notify_roles):
    wh_id = uuid.uuid4()
    target_date = date.today() + timedelta(days=2)
    farmer = _make_user()
    wh = _make_warehouse(wh_id=wh_id, total_kg=Decimal("500000"), current_kg=Decimal("100000"))

    db = AsyncMock()
    wh_res = MagicMock()
    wh_res.scalar_one_or_none.return_value = wh

    dup_res = MagicMock()
    dup_res.scalar_one_or_none.return_value = None

    db.execute.side_effect = [wh_res, dup_res]

    booking = await booking_service.create_booking(
        db,
        farmer=farmer,
        warehouse_id=wh_id,
        warehouse_slot_id=None,
        booking_date=target_date,
        delivery_address="Plot 1",
        grain_type="Cotton (Kapus)",
        quantity_kg=Decimal("1500"),
        grain_sale_id=None,
        notes=None,
    )

    assert booking.warehouse_slot_id is None
    assert booking.status == BookingStatus.PENDING
    assert wh.current_load_kg == Decimal("101500")


# 2. Insufficient physical warehouse capacity -> rejected
async def test_insufficient_physical_capacity_rejected():
    wh_id = uuid.uuid4()
    farmer = _make_user()
    wh = _make_warehouse(wh_id=wh_id, total_kg=Decimal("10000"), current_kg=Decimal("9500"))  # 500 kg left

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
            quantity_kg=Decimal("600"),  # 600 > 500
            grain_sale_id=None,
            notes=None,
        )

    assert "capacity exceeded" in str(exc_info.value).lower()


# 3. Inactive warehouse -> rejected
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


# 4. Past date -> rejected
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


# 5. Duplicate active booking on same warehouse and date -> rejected
async def test_duplicate_active_farmer_booking_rejected():
    wh_id = uuid.uuid4()
    target_date = date.today() + timedelta(days=1)
    farmer = _make_user()
    wh = _make_warehouse(wh_id=wh_id)

    existing_booking = BookingSlot(
        id=uuid.uuid4(),
        farmer_id=farmer.id,
        warehouse_id=wh_id,
        warehouse_slot_id=None,
        booking_date=target_date,
        status=BookingStatus.PENDING,
    )

    db = AsyncMock()
    wh_res = MagicMock()
    wh_res.scalar_one_or_none.return_value = wh
    dup_res = MagicMock()
    dup_res.scalar_one_or_none.return_value = existing_booking
    db.execute.side_effect = [wh_res, dup_res]

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

    assert "already have an active booking" in str(exc_info.value).lower()


# 6. Successful booking increments warehouse current_load_kg
@patch("app.services.notification_service.notify_roles", new_callable=AsyncMock)
@patch("app.services.notification_service.notify_user", new_callable=AsyncMock)
@patch("app.services.audit_service.record", new_callable=AsyncMock)
async def test_booking_increments_warehouse_current_load(mock_audit, mock_notify_user, mock_notify_roles):
    wh_id = uuid.uuid4()
    farmer = _make_user()
    wh = _make_warehouse(wh_id=wh_id, total_kg=Decimal("100000"), current_kg=Decimal("20000"))

    db = AsyncMock()
    wh_res = MagicMock()
    wh_res.scalar_one_or_none.return_value = wh
    dup_res = MagicMock()
    dup_res.scalar_one_or_none.return_value = None
    db.execute.side_effect = [wh_res, dup_res]

    await booking_service.create_booking(
        db,
        farmer=farmer,
        warehouse_id=wh_id,
        warehouse_slot_id=None,
        booking_date=date.today() + timedelta(days=1),
        delivery_address="Farm",
        grain_type="Paddy",
        quantity_kg=Decimal("5000"),
        grain_sale_id=None,
        notes=None,
    )

    assert wh.current_load_kg == Decimal("25000")


# 7. Cancellation releases warehouse capacity
@patch("app.services.notification_service.notify_user", new_callable=AsyncMock)
@patch("app.services.audit_service.record", new_callable=AsyncMock)
async def test_cancellation_releases_warehouse_capacity(mock_audit, mock_notify_user):
    wh_id = uuid.uuid4()
    actor = _make_user(role=UserRole.MANAGER)
    wh = _make_warehouse(wh_id=wh_id, current_kg=Decimal("50000"))

    booking = BookingSlot(
        id=uuid.uuid4(),
        farmer_id=uuid.uuid4(),
        warehouse_id=wh_id,
        warehouse_slot_id=None,
        quantity_kg=Decimal("5000"),
        status=BookingStatus.PENDING,
    )

    db = AsyncMock()
    b_res = MagicMock()
    b_res.scalar_one_or_none.return_value = booking
    wh_res = MagicMock()
    wh_res.scalar_one_or_none.return_value = wh

    db.execute.side_effect = [b_res, wh_res]

    cancelled = await booking_service.update_booking_status(
        db, actor=actor, booking_id=booking.id, new_status=BookingStatus.CANCELLED, notes=None
    )
    assert cancelled.status == BookingStatus.CANCELLED
    assert wh.current_load_kg == Decimal("45000")


# 8. warehouse_slot_id remains NULL for normal Farmer booking
@patch("app.services.notification_service.notify_roles", new_callable=AsyncMock)
@patch("app.services.notification_service.notify_user", new_callable=AsyncMock)
@patch("app.services.audit_service.record", new_callable=AsyncMock)
async def test_warehouse_slot_id_remains_null(mock_audit, mock_notify_user, mock_notify_roles):
    wh_id = uuid.uuid4()
    farmer = _make_user()
    wh = _make_warehouse(wh_id=wh_id)

    db = AsyncMock()
    wh_res = MagicMock()
    wh_res.scalar_one_or_none.return_value = wh
    dup_res = MagicMock()
    dup_res.scalar_one_or_none.return_value = None
    db.execute.side_effect = [wh_res, dup_res]

    booking = await booking_service.create_booking(
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

    assert booking.warehouse_slot_id is None


# 9. Optional explicit manager slot booking binds slot
@patch("app.services.notification_service.notify_roles", new_callable=AsyncMock)
@patch("app.services.notification_service.notify_user", new_callable=AsyncMock)
@patch("app.services.audit_service.record", new_callable=AsyncMock)
async def test_optional_explicit_manager_slot_booking(mock_audit, mock_notify_user, mock_notify_roles):
    wh_id = uuid.uuid4()
    slot_id = uuid.uuid4()
    target_date = date.today() + timedelta(days=1)
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
    assert slot.booked_kg == Decimal("3500")
    assert wh.current_load_kg == Decimal("101500")


# 10. Weather Service: fresh upstream response displayed and cached
@patch("app.services.weather_service.is_available", return_value=False)
@patch("httpx.AsyncClient.get")
async def test_weather_fresh_response(mock_http_get, mock_redis_avail):
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "current": {
            "temperature_2m": 32.5,
            "apparent_temperature": 34.0,
            "relative_humidity_2m": 60,
            "wind_speed_10m": 12.0,
            "weather_code": 0,
            "precipitation": 0,
        }
    }
    mock_http_get.return_value = mock_resp

    w = await weather_service.current_weather(15.82, 78.03)
    assert w is not None
    assert w["temperature_c"] == 32
    assert w["condition"] == "Clear"


# 11. Weather Service: temporary failure uses stale Redis cache
@patch("app.services.weather_service.is_available", return_value=True)
@patch("app.services.weather_service.get_redis")
@patch("httpx.AsyncClient.get")
async def test_weather_temporary_failure_uses_stale_cache(mock_http_get, mock_get_redis, mock_redis_avail):
    mock_http_get.side_effect = Exception("Upstream timeout")
    
    mock_redis = AsyncMock()
    mock_redis.get.side_effect = [
        None,  # fresh cache miss
        '{"temperature_c": 30, "condition": "Clear", "humidity_percent": 50, "wind_kmh": 10, "advisory": "Good day to irrigate"}' # stale cache hit
    ]
    mock_get_redis.return_value = mock_redis

    w = await weather_service.current_weather(15.82, 78.03)
    assert w is not None
    assert w["is_stale"] is True
    assert w["temperature_c"] == 30


# 12. Weather Service: no cache and failure returns None (unavailable state)
@patch("app.services.weather_service.is_available", return_value=True)
@patch("app.services.weather_service.get_redis")
@patch("httpx.AsyncClient.get")
async def test_weather_no_cache_returns_none(mock_http_get, mock_get_redis, mock_redis_avail):
    mock_http_get.side_effect = Exception("Upstream failure")
    mock_redis = AsyncMock()
    mock_redis.get.return_value = None
    mock_get_redis.return_value = mock_redis

    w = await weather_service.current_weather(15.82, 78.03)
    assert w is None
