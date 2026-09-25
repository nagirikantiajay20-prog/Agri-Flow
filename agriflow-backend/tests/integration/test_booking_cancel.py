"""
Booking cancel-own fix — see backend README "Known simplifications"
(now resolved) and app.services.booking_service.update_booking_status.
"""
from datetime import date, time
from decimal import Decimal

import pytest

from tests.utils import auth_headers

pytestmark = pytest.mark.asyncio


async def _create_warehouse_and_slot(db_session):
    from app.models.warehouse import Warehouse, WarehouseSlot

    warehouse = Warehouse(name="Cancel Test WH", address="Addr", total_capacity_kg=Decimal("1000"))
    db_session.add(warehouse)
    await db_session.flush()
    slot = WarehouseSlot(
        warehouse_id=warehouse.id, slot_date=date.today(), start_time=time(9, 0), end_time=time(11, 0),
        capacity_kg=Decimal("100"), booked_kg=Decimal("0"), status="active",
    )
    db_session.add(slot)
    await db_session.commit()
    return warehouse, slot


async def test_farmer_can_cancel_own_booking(client, db_session, active_farmer):
    warehouse, slot = await _create_warehouse_and_slot(db_session)

    create_resp = await client.post(
        "/api/v1/bookings",
        json={
            "warehouse_id": str(warehouse.id), "warehouse_slot_id": str(slot.id),
            "booking_date": str(date.today()), "delivery_address": "123 Farm Rd",
            "grain_type": "Rice", "quantity_kg": "10",
        },
        headers=auth_headers(active_farmer),
    )
    assert create_resp.status_code == 201
    booking_id = create_resp.json()["data"]["id"]

    cancel_resp = await client.patch(
        f"/api/v1/bookings/{booking_id}/status",
        json={"status": "cancelled"},
        headers=auth_headers(active_farmer),
    )
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["data"]["status"] == "cancelled"

    # Capacity should be released back to the slot.
    slots_resp = await client.get(f"/api/v1/warehouses/{warehouse.id}/slots", headers=auth_headers(active_farmer))
    matching = [s for s in slots_resp.json()["data"] if s["id"] == str(slot.id)][0]
    assert matching["booked_kg"] == "0.00"


async def test_farmer_cannot_confirm_own_booking(client, db_session, active_farmer):
    """Farmers may only cancel — confirming/completing stays manager-only."""
    warehouse, slot = await _create_warehouse_and_slot(db_session)
    create_resp = await client.post(
        "/api/v1/bookings",
        json={
            "warehouse_id": str(warehouse.id), "warehouse_slot_id": str(slot.id),
            "booking_date": str(date.today()), "delivery_address": "123 Farm Rd",
            "grain_type": "Rice", "quantity_kg": "10",
        },
        headers=auth_headers(active_farmer),
    )
    booking_id = create_resp.json()["data"]["id"]

    resp = await client.patch(
        f"/api/v1/bookings/{booking_id}/status",
        json={"status": "confirmed"},
        headers=auth_headers(active_farmer),
    )
    assert resp.status_code == 403


async def test_farmer_cannot_cancel_another_farmers_booking(client, db_session, active_farmer):
    from app.core.security import hash_password
    from app.models.enums import UserRole, UserStatus
    from app.models.user import User

    warehouse, slot = await _create_warehouse_and_slot(db_session)
    other = User(
        name="Other Farmer 2", phone="9333344455", password_hash=hash_password("x"),
        role=UserRole.FARMER, status=UserStatus.ACTIVE,
    )
    db_session.add(other)
    await db_session.commit()

    create_resp = await client.post(
        "/api/v1/bookings",
        json={
            "warehouse_id": str(warehouse.id), "warehouse_slot_id": str(slot.id),
            "booking_date": str(date.today()), "delivery_address": "123 Farm Rd",
            "grain_type": "Rice", "quantity_kg": "10",
        },
        headers=auth_headers(other),
    )
    booking_id = create_resp.json()["data"]["id"]

    resp = await client.patch(
        f"/api/v1/bookings/{booking_id}/status",
        json={"status": "cancelled"},
        headers=auth_headers(active_farmer),
    )
    assert resp.status_code == 403
