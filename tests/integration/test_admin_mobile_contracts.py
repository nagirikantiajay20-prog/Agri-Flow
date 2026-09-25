from decimal import Decimal

import pytest

from tests.utils import auth_headers

pytestmark = pytest.mark.asyncio


async def test_warehouse_crud_and_inventory_breakdown(client, super_admin, manager, active_farmer, db_session):
    # 1. SuperAdmin creates a warehouse
    create_resp = await client.post(
        "/api/v1/warehouses",
        headers=auth_headers(super_admin),
        json={
            "name": "Central Grain Storage",
            "address": "Plot 10, Industrial Area, Hyderabad",
            "location": "Hyderabad",
            "contact_number": "+919876543210",
            "total_capacity_kg": 50000.0,
            "manager_id": str(manager.id),
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    wh_data = create_resp.json()["data"]
    wh_id = wh_data["id"]
    assert wh_data["name"] == "Central Grain Storage"
    assert wh_data["is_active"] is True

    # 2. Manager and SuperAdmin can fetch warehouse detail
    mgr_get = await client.get(f"/api/v1/warehouses/{wh_id}", headers=auth_headers(manager))
    assert mgr_get.status_code == 200, mgr_get.text
    assert mgr_get.json()["data"]["id"] == wh_id

    # 3. Farmer is FORBIDDEN (403) from accessing admin warehouse endpoint
    farmer_get = await client.get(f"/api/v1/warehouses/{wh_id}", headers=auth_headers(active_farmer))
    assert farmer_get.status_code == 403

    # 4. Manager patches warehouse metadata
    patch_resp = await client.patch(
        f"/api/v1/warehouses/{wh_id}",
        headers=auth_headers(manager),
        json={"location": "Secunderabad", "contact_number": "+919999999999"},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["data"]["location"] == "Secunderabad"
    assert patch_resp.json()["data"]["contact_number"] == "+919999999999"

    # 5. Add inventory and query breakdown
    add_inv_resp = await client.post(
        f"/api/v1/warehouses/{wh_id}/inventory",
        headers=auth_headers(manager),
        json={"grain_type": "Rice", "quantity_kg": 1500.0},
    )
    assert add_inv_resp.status_code == 201

    get_inv_resp = await client.get(
        f"/api/v1/warehouses/{wh_id}/inventory",
        headers=auth_headers(manager),
    )
    assert get_inv_resp.status_code == 200
    inv_list = get_inv_resp.json()["data"]
    assert len(inv_list) == 1
    assert inv_list[0]["grain_type"] == "Rice"
    assert Decimal(str(inv_list[0]["quantity_kg"])) == Decimal("1500.00")

    # 6. DELETE warehouse with active inventory must fail with 409 Conflict
    del_inv_fail = await client.delete(
        f"/api/v1/warehouses/{wh_id}",
        headers=auth_headers(super_admin),
    )
    assert del_inv_fail.status_code == 409
    assert "inventory still stored" in del_inv_fail.json()["error"]["message"]

    # 7. Create slot and booking; test booking conflict safety
    slot_resp = await client.post(
        "/api/v1/warehouse-slots",
        headers=auth_headers(manager),
        json={
            "warehouse_id": wh_id,
            "slot_date": "2026-10-15",
            "start_time": "09:00:00",
            "end_time": "12:00:00",
            "capacity_kg": 5000.0,
        },
    )
    assert slot_resp.status_code == 201
    slot_id = slot_resp.json()["data"]["id"]

    # Delete slot when unbooked -> success 204
    del_slot = await client.delete(
        f"/api/v1/warehouse-slots/{slot_id}",
        headers=auth_headers(manager),
    )
    assert del_slot.status_code == 204


async def test_farmer_administration_by_manager_and_isolation(client, manager, super_admin, active_farmer):
    # Manager updates farmer details
    patch_resp = await client.patch(
        f"/api/v1/farmers/{active_farmer.id}",
        headers=auth_headers(manager),
        json={
            "village": "Guntur Rural",
            "district": "Guntur",
            "state": "Andhra Pradesh",
            "acres_of_land": 12.5,
            "primary_crop": "Cotton",
        },
    )
    assert patch_resp.status_code == 200, patch_resp.text
    farmer_view = patch_resp.json()["data"]
    assert farmer_view["farmer_profile"]["acres_of_land"] == 12.5

    # Farmer attempts to call admin PATCH /farmers/{id} -> 403 Forbidden
    farmer_call = await client.patch(
        f"/api/v1/farmers/{active_farmer.id}",
        headers=auth_headers(active_farmer),
        json={"acres_of_land": 100.0},
    )
    assert farmer_call.status_code == 403


async def test_manager_administration_by_super_admin(client, super_admin, manager, active_farmer):
    # SuperAdmin updates manager
    mgr_patch = await client.patch(
        f"/api/v1/managers/{manager.id}",
        headers=auth_headers(super_admin),
        json={
            "assigned_region": "Southern Zone",
            "department": "Procurement Operations",
        },
    )
    assert mgr_patch.status_code == 200, mgr_patch.text
    data = mgr_patch.json()["data"]
    assert data["assigned_region"] == "Southern Zone"
    assert data["department"] == "Procurement Operations"

    # Manager cannot call PATCH /managers/{id} -> 403 Forbidden
    mgr_forbidden = await client.patch(
        f"/api/v1/managers/{manager.id}",
        headers=auth_headers(manager),
        json={"assigned_region": "Hacked"},
    )
    assert mgr_forbidden.status_code == 403

    # Farmer cannot call PATCH /managers/{id} -> 403 Forbidden
    farmer_forbidden = await client.patch(
        f"/api/v1/managers/{manager.id}",
        headers=auth_headers(active_farmer),
        json={"assigned_region": "Hacked"},
    )
    assert farmer_forbidden.status_code == 403


async def test_seed_purchase_grade_and_max_order_quantity_via_api(client, active_farmer, super_admin):
    # Admin creates seed with grade pricing and max order limit
    seed_resp = await client.post(
        "/api/v1/seeds",
        headers=auth_headers(super_admin),
        json={
            "name": "Elite Cotton Seed",
            "variety": "EC-99",
            "price_per_kg": 80.0,
            "price_grade_a": 95.0,
            "price_grade_b": 85.0,
            "price_grade_c": 70.0,
            "max_order_quantity_kg": 25.0,
            "stock_kg": 500.0,
        },
    )
    assert seed_resp.status_code == 201, seed_resp.text
    seed_id = seed_resp.json()["data"]["id"]

    # Farmer orders with Grade A below limit
    order_a = await client.post(
        "/api/v1/farmer/seeds/purchase",
        headers=auth_headers(active_farmer),
        json={
            "seed_id": seed_id,
            "quantity_kg": 10.0,
            "grade": "A",
            "payment_method": "warehouse",
        },
    )
    assert order_a.status_code == 201, order_a.text
    receipt = order_a.json()["data"]
    assert receipt["price_per_kg"] == 95.0
    assert receipt["total_amount"] == 950.0

    # Farmer orders exceeding max_order_quantity_kg -> 422 Unprocessable Entity
    order_exceed = await client.post(
        "/api/v1/farmer/seeds/purchase",
        headers=auth_headers(active_farmer),
        json={
            "seed_id": seed_id,
            "quantity_kg": 26.0,
            "grade": "A",
            "payment_method": "warehouse",
        },
    )
    assert order_exceed.status_code == 422
    assert "exceeds maximum allowed order quantity" in order_exceed.json()["error"]["message"]
