"""
End-to-end behaviour of the farmer mobile API (/api/v1/farmer).

Authorization is covered exhaustively by tests/security/test_rbac_matrix.py;
this file checks what each screen actually does against real Postgres:
login rules, ownership, stock holds, slot limits, documents, dashboard.
"""
from __future__ import annotations

import uuid
from datetime import date, time, timedelta
from decimal import Decimal

import pytest

from app.core.security import hash_password
from app.integrations import storage
from app.models.crop import Crop, FarmVisit
from app.models.enums import UserRole, UserStatus
from app.models.ledger import MarketRate
from app.models.seed import Seed
from app.models.user import FarmerProfile, FcmDeviceToken, User
from app.models.warehouse import Warehouse, WarehouseSlot
from tests.utils import auth_headers

pytestmark = pytest.mark.asyncio

API = "/api/v1/farmer"


async def _farmer(db, *, status=UserStatus.ACTIVE, phone=None, **profile) -> User:
    user = User(
        name="App Farmer",
        phone=phone or f"9{uuid.uuid4().int % 10**9:09d}",
        password_hash=hash_password("farmerpass1"),
        role=UserRole.FARMER,
        status=status,
    )
    db.add(user)
    await db.flush()
    db.add(FarmerProfile(user_id=user.id, **profile))
    await db.commit()
    return user


async def _seed(db, *, stock="100", price="85") -> Seed:
    seed = Seed(name="TDN-58 Groundnut", crop_type="Groundnut", variety="High Oil",
                price_per_kg=Decimal(price), old_price=Decimal("98"), stock_kg=Decimal(stock))
    db.add(seed)
    await db.commit()
    return seed


async def _slot(db, *, capacity="1000", max_bookings=10, slot_date=None) -> WarehouseSlot:
    warehouse = Warehouse(name="Kurnool Central", address="NH44, Kurnool", total_capacity_kg=Decimal("100000"),
                          current_load_kg=Decimal("35000"), contact_number="9000000000")
    db.add(warehouse)
    await db.flush()
    slot = WarehouseSlot(warehouse_id=warehouse.id, slot_date=slot_date or date.today() + timedelta(days=2),
                         start_time=time(9, 0), end_time=time(12, 0), capacity_kg=Decimal(capacity),
                         booked_kg=Decimal("0"), max_bookings=max_bookings, status="active")
    db.add(slot)
    await db.commit()
    return slot


# ── Auth ────────────────────────────────────────────────────────────────
async def test_login_accepts_a_formatted_mobile_number_and_returns_profile(client, fake_redis, db_session):
    farmer = await _farmer(db_session, phone="9502662924", farm_name="Kaveri Farm",
                           account_number="10293847561", acres_of_land=Decimal("12"))
    resp = await client.post(f"{API}/auth/login", json={"phone": "+91 95026 62924", "password": "farmerpass1"})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["access_token"] and data["refresh_token"]
    assert data["user"]["id"] == str(farmer.id)
    assert data["farmer_profile"]["farm_name"] == "Kaveri Farm"
    assert data["farmer_profile"]["acres_of_land"] == 12.0
    assert data["farmer_profile"]["bank_account_number"] == "*******7561"


async def test_login_rejects_pending_and_staff_accounts(client, fake_redis, db_session, manager):
    pending = await _farmer(db_session, status=UserStatus.PENDING)
    resp = await client.post(f"{API}/auth/login", json={"phone": pending.phone, "password": "farmerpass1"})
    assert resp.status_code == 403
    assert "approval" in resp.json()["error"]["message"]

    resp = await client.post(f"{API}/auth/login", json={"phone": manager.phone, "password": "testpass123"})
    assert resp.status_code == 403


async def test_wrong_password_is_401_without_revealing_account_state(client, fake_redis, db_session):
    pending = await _farmer(db_session, status=UserStatus.PENDING)
    resp = await client.post(f"{API}/auth/login", json={"phone": pending.phone, "password": "wrong-password"})
    assert resp.status_code == 401


async def test_refresh_rotates_tokens(client, fake_redis, db_session):
    farmer = await _farmer(db_session)
    login = (await client.post(f"{API}/auth/login", json={"phone": farmer.phone, "password": "farmerpass1"})).json()
    first = login["data"]["refresh_token"]
    rotated = await client.post(f"{API}/auth/refresh", json={"refresh_token": first})
    assert rotated.status_code == 200
    assert rotated.json()["data"]["refresh_token"] != first
    replay = await client.post(f"{API}/auth/refresh", json={"refresh_token": first})
    assert replay.status_code == 401, "A rotated refresh token must not be usable again"


# ── Profile ─────────────────────────────────────────────────────────────
async def test_profile_update_rejects_privileged_fields(client, fake_redis, db_session):
    farmer = await _farmer(db_session)
    headers = auth_headers(farmer)
    ok = await client.patch(f"{API}/profile", headers=headers, json={"village": "Kalluru", "acres_of_land": 7.5})
    assert ok.status_code == 200
    assert ok.json()["data"]["village"] == "Kalluru"
    assert ok.json()["data"]["acres_of_land"] == 7.5

    for field, value in (("status", "active"), ("role", "super_admin"), ("user_id", str(uuid.uuid4()))):
        bad = await client.patch(f"{API}/profile", headers=headers, json={field: value})
        assert bad.status_code == 422, f"{field} must be rejected, got {bad.status_code}"


async def test_bank_request_does_not_change_bank_details_until_approved(client, fake_redis, db_session):
    farmer = await _farmer(db_session, account_number="11112222333")
    headers = auth_headers(farmer)
    resp = await client.post(f"{API}/profile/bank-request", headers=headers, json={
        "bank_name": "State Bank of India", "account_number": "10293847561", "ifsc_code": "sbin0001234",
    })
    assert resp.status_code == 201
    assert resp.json()["data"]["status"] == "pending"
    profile = (await client.get(f"{API}/profile", headers=headers)).json()["data"]
    assert profile["bank_account_number"].endswith("2333"), "Details change only after review"


# ── Crops ───────────────────────────────────────────────────────────────
async def test_crop_lifecycle_and_ownership(client, fake_redis, db_session):
    owner = await _farmer(db_session)
    other = await _farmer(db_session)
    headers = auth_headers(owner)

    created = await client.post(f"{API}/crops", headers=headers, json={
        "crop_name": "Cotton Plot B", "crop_type": "Cotton", "acres": 6.8,
        "sowing_date": "2026-07-01", "harvest_date": "2026-12-15", "status": "Sowing", "location": "Near canal",
    })
    assert created.status_code == 201, created.text
    body = created.json()["data"]
    crop_id = body["crop"]["id"]
    assert [v["visit_month"] for v in body["visits"]] == [1, 4], "Cotton is inspected at months 1 and 4"
    assert body["crop"]["notes"] == "Near canal"

    updated = await client.patch(
        f"{API}/crops/{crop_id}",
        headers=headers,
        json={"status": "Growing", "acres": 7, "farmer_comment": "the crop has been sowed well"},
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["status"] == "Growing"

    stolen = await client.patch(f"{API}/crops/{crop_id}", headers=auth_headers(other), json={"acres": 1})
    assert stolen.status_code == 404, "Another farmer's crop must be indistinguishable from a missing one"
    assert (
        await client.request("DELETE", f"{API}/crops/{crop_id}", headers=auth_headers(other), json={"reason": "Testing stolen"})
    ).status_code == 404

    assert len((await client.get(f"{API}/crops/{crop_id}/inspections", headers=headers)).json()["data"]) == 2

    # Soft delete: the crop disappears from the active list, but its visit
    # history, and the crop row itself, are never physically destroyed.
    deleted = await client.request("DELETE", f"{API}/crops/{crop_id}", headers=headers, json={"reason": "Harvest complete"})
    assert deleted.status_code == 200

    active = (await client.get(f"{API}/crops", headers=headers)).json()["data"]
    assert crop_id not in [c["id"] for c in active], "A deleted crop must not appear in the active list"

    history = (await client.get(f"{API}/crops?include_closed=true", headers=headers)).json()["data"]
    deleted_entry = next(c for c in history if c["id"] == crop_id)
    assert deleted_entry["is_deleted"] is True
    assert deleted_entry["deleted_at"] is not None

    visits_still_present = (await client.get(f"{API}/crops/visits", headers=headers)).json()["data"]
    assert len(visits_still_present) == 2, "Visit/inspection history must survive a crop soft-delete"

    inspections_after_delete = (
        await client.get(f"{API}/crops/{crop_id}/inspections", headers=headers)
    ).json()["data"]
    assert len(inspections_after_delete) == 2, "Inspection history must remain reachable by crop id"

    # Idempotent: deleting an already-deleted crop is not an error.
    redeleted = await client.delete(f"{API}/crops/{crop_id}", headers=headers)
    assert redeleted.status_code == 200

    # A deleted crop is frozen: no further edits or scans.
    edit_attempt = await client.patch(f"{API}/crops/{crop_id}", headers=headers, json={"acres": 3})
    assert edit_attempt.status_code == 409

    scan_attempt = await client.post(
        f"{API}/crops/{crop_id}/scan", headers=headers,
        files={"image": ("leaf.jpg", b"\xff\xd8jpeg", "image/jpeg")},
    )
    assert scan_attempt.status_code == 409


async def test_scan_uploads_image_and_creates_pending_review(client, fake_redis, db_session, monkeypatch):
    farmer = await _farmer(db_session)
    other = await _farmer(db_session)
    crop = Crop(farmer_id=farmer.id, crop_type="Rice", acres=Decimal("4.2"), sowing_date=date.today() - timedelta(days=45))
    db_session.add(crop)
    await db_session.commit()

    uploads = []

    async def fake_upload(**kwargs):
        uploads.append(kwargs)
        return {"object_path": f"crop-scans/{kwargs['object_name']}.jpg", "size_bytes": len(kwargs["data"])}

    monkeypatch.setattr(storage, "upload_bytes", fake_upload)
    monkeypatch.setattr(storage, "read_url", lambda path: f"https://signed.example/{path}" if path else None)

    denied = await client.post(f"{API}/crops/{crop.id}/scan", headers=auth_headers(other),
                               files={"image": ("leaf.jpg", b"\xff\xd8jpeg", "image/jpeg")})
    assert denied.status_code == 404
    assert uploads == [], "Nothing may be uploaded against another farmer's crop"

    resp = await client.post(f"{API}/crops/{crop.id}/scan", headers=auth_headers(farmer),
                             files={"image": ("leaf.jpg", b"\xff\xd8jpeg", "image/jpeg")}, data={"notes": "yellowing"})
    assert resp.status_code == 201, resp.text
    inspection = resp.json()["data"]["inspection"]
    assert inspection["status"] == "pending_review"
    assert inspection["visit_month"] == 2
    assert inspection["image_url"].startswith("https://signed.example/crop-scans/")
    assert uploads[0]["object_name"].startswith(f"farmer_{farmer.id}/{crop.id}_")


# ── Seeds ───────────────────────────────────────────────────────────────
async def test_seed_filters(client, fake_redis, db_session):
    farmer = await _farmer(db_session)
    await _seed(db_session, price="85")
    db_session.add(Seed(name="Bt Cotton Gold", crop_type="Cotton", price_per_kg=Decimal("900"), stock_kg=Decimal("0")))
    await db_session.commit()
    headers = auth_headers(farmer)

    names = lambda r: {x["name"] for x in r.json()["data"]}  # noqa: E731
    assert "Bt Cotton Gold" in names(await client.get(f"{API}/seeds?crop_type=Cotton", headers=headers))
    assert "Bt Cotton Gold" not in names(await client.get(f"{API}/seeds?in_stock_only=true", headers=headers))
    assert names(await client.get(f"{API}/seeds?q=tdn&max_price=100", headers=headers)) == {"TDN-58 Groundnut"}


async def test_purchase_holds_stock_and_payment_resolves_the_hold(client, fake_redis, db_session, super_admin):
    farmer = await _farmer(db_session)
    seed = await _seed(db_session, stock="100", price="85")
    headers = auth_headers(farmer)

    resp = await client.post(f"{API}/seeds/purchase", headers=headers, json={
        "seed_id": str(seed.id), "quantity_kg": 5, "grade": "A", "payment_method": "warehouse",
    })
    assert resp.status_code == 201, resp.text
    receipt = resp.json()["data"]
    assert receipt["total_amount"] == 425.0
    assert receipt["payment_status_label"] == "Unpaid (Pay at Warehouse)"
    assert receipt["invoice_number"].startswith("SP-")

    await db_session.refresh(seed)
    assert seed.stock_kg == Decimal("95") and seed.on_hold_kg == Decimal("5")

    paid = await client.patch(f"/api/v1/seed-purchases/{receipt['order_id']}/status",
                              headers=auth_headers(super_admin), json={"payment_status": "paid"})
    assert paid.status_code == 200
    await db_session.refresh(seed)
    assert seed.stock_kg == Decimal("95") and seed.on_hold_kg == Decimal("0")

    history = (await client.get(f"{API}/seeds/purchases", headers=headers)).json()
    assert history["pagination"]["total"] == 1
    assert history["data"][0]["seed"]["name"] == "TDN-58 Groundnut"


async def test_failed_purchase_returns_stock(client, fake_redis, db_session, super_admin):
    farmer = await _farmer(db_session)
    seed = await _seed(db_session, stock="10")
    order = (await client.post(f"{API}/seeds/purchase", headers=auth_headers(farmer),
                               json={"seed_id": str(seed.id), "quantity_kg": 4})).json()["data"]
    await client.patch(f"/api/v1/seed-purchases/{order['order_id']}/status",
                       headers=auth_headers(super_admin), json={"payment_status": "failed"})
    await db_session.refresh(seed)
    assert seed.stock_kg == Decimal("10") and seed.on_hold_kg == Decimal("0")


async def test_purchase_over_stock_is_409_with_error_code(client, fake_redis, db_session):
    farmer = await _farmer(db_session)
    seed = await _seed(db_session, stock="3")
    resp = await client.post(f"{API}/seeds/purchase", headers=auth_headers(farmer),
                             json={"seed_id": str(seed.id), "quantity_kg": 5})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "INSUFFICIENT_STOCK"


# ── Warehouses & bookings ───────────────────────────────────────────────
async def test_slot_listing_booking_limit_and_cancellation(client, fake_redis, db_session):
    slot = await _slot(db_session, capacity="5000", max_bookings=1)
    first, second = await _farmer(db_session), await _farmer(db_session)

    listed = (await client.get(f"{API}/warehouses/{slot.warehouse_id}/slots?date={slot.slot_date}",
                               headers=auth_headers(first))).json()["data"]
    assert listed[0]["slot_time"] == "09:00 AM - 12:00 PM"
    assert listed[0]["available_bookings"] == 1
    assert listed[0]["available_weight_qtl"] == 50.0

    payload = {"warehouse_id": str(slot.warehouse_id), "warehouse_slot_id": str(slot.id), "grain_type": "Cotton",
               "quantity_kg": 2500, "booking_date": str(slot.slot_date), "delivery_address": "Kalluru village"}
    booked = await client.post(f"{API}/grain-sales/book-slot", headers=auth_headers(first), json=payload)
    assert booked.status_code == 201, booked.text

    full = await client.post(f"{API}/grain-sales/book-slot", headers=auth_headers(second), json=payload)
    assert full.status_code == 409, "The slot's max_bookings must be enforced"

    booking_id = booked.json()["data"]["booking_id"]
    bookings = (await client.get(f"{API}/grain-sales/bookings", headers=auth_headers(first))).json()["data"]
    assert bookings[0]["warehouse"]["name"] == "Kurnool Central"
    assert (await client.delete(f"{API}/grain-sales/bookings/{booking_id}", headers=auth_headers(second))).status_code == 403

    assert (await client.delete(f"{API}/grain-sales/bookings/{booking_id}", headers=auth_headers(first))).status_code == 200
    await db_session.refresh(slot)
    assert slot.current_booking_count == 0 and slot.booked_kg == Decimal("0")
    assert (await client.post(f"{API}/grain-sales/book-slot", headers=auth_headers(second), json=payload)).status_code == 201

    # "Cancel" is a status transition, never a row delete: the booking
    # remains in history with status=cancelled, not physically removed.
    from sqlalchemy import select

    from app.models.warehouse import BookingSlot

    cancelled_row = (
        await db_session.execute(select(BookingSlot).where(BookingSlot.id == uuid.UUID(booking_id)))
    ).scalar_one_or_none()
    assert cancelled_row is not None, "A cancelled booking must remain in the database, not be deleted"
    assert cancelled_row.status.value == "cancelled"

    history = (await client.get(f"{API}/grain-sales/bookings?status=cancelled", headers=auth_headers(first))).json()["data"]
    assert any(b["id"] == booking_id for b in history), "Cancelled bookings must remain visible in booking history"


async def test_booking_date_must_match_the_slot(client, fake_redis, db_session):
    slot = await _slot(db_session)
    farmer = await _farmer(db_session)
    resp = await client.post(f"{API}/grain-sales/book-slot", headers=auth_headers(farmer), json={
        "warehouse_id": str(slot.warehouse_id), "warehouse_slot_id": str(slot.id), "grain_type": "Rice",
        "quantity_kg": 100, "booking_date": str(slot.slot_date + timedelta(days=1)), "delivery_address": "addr",
    })
    assert resp.status_code == 422


async def test_warehouse_directory_reports_available_capacity(client, fake_redis, db_session):
    await _slot(db_session)
    farmer = await _farmer(db_session)
    warehouses = (await client.get(f"{API}/warehouses", headers=auth_headers(farmer))).json()["data"]
    kurnool = next(w for w in warehouses if w["name"] == "Kurnool Central")
    assert kurnool["capacity"] == 100000.0 and kurnool["available_capacity"] == 65000.0


# ── Grain offers ────────────────────────────────────────────────────────
async def test_offer_cannot_reference_another_farmers_crop(client, fake_redis, db_session):
    owner, other = await _farmer(db_session), await _farmer(db_session)
    crop = Crop(farmer_id=owner.id, crop_type="Cotton", acres=Decimal("3"), sowing_date=date.today())
    db_session.add(crop)
    await db_session.commit()

    stolen = await client.post(f"{API}/grain-sales/offers", headers=auth_headers(other),
                               json={"crop_type": "Cotton", "quantity_kg": 100, "crop_id": str(crop.id)})
    assert stolen.status_code == 404

    ok = await client.post(f"{API}/grain-sales/offers", headers=auth_headers(owner),
                           json={"crop_type": "Cotton", "grade": "A", "quantity_kg": 2500, "price_per_kg": 71.25})
    assert ok.status_code == 201
    assert ok.json()["data"]["offered_price_per_kg"] == 71.25
    assert ok.json()["data"]["status"] == "pending"


# ── Notifications & devices ─────────────────────────────────────────────
async def test_unread_count_and_read_all(client, fake_redis, db_session):
    from app.services import notification_service

    farmer = await _farmer(db_session)
    for i in range(3):
        await notification_service.notify_user(db_session, user_id=farmer.id, title=f"n{i}", message="m")
    await db_session.commit()
    headers = auth_headers(farmer)
    assert (await client.get(f"{API}/notifications/unread-count", headers=headers)).json()["data"]["unread_count"] == 3
    assert (await client.post(f"{API}/notifications/read-all", headers=headers)).status_code == 200
    assert (await client.get(f"{API}/notifications/unread-count", headers=headers)).json()["data"]["unread_count"] == 0


async def test_fcm_token_follows_the_device_to_its_new_user(client, fake_redis, db_session):
    from sqlalchemy import select

    first, second = await _farmer(db_session), await _farmer(db_session)
    token = "fcm-device-token-" + uuid.uuid4().hex
    for farmer in (first, first, second):
        resp = await client.post(f"{API}/notifications/fcm-token", headers=auth_headers(farmer),
                                 json={"fcm_token": token, "device_type": "android"})
        assert resp.status_code == 200
    rows = (await db_session.execute(select(FcmDeviceToken).where(FcmDeviceToken.fcm_token == token))).scalars().all()
    assert len(rows) == 1 and rows[0].user_id == second.id


# ── Documents ───────────────────────────────────────────────────────────
async def test_document_upload_stores_a_path_and_returns_a_signed_url(client, fake_redis, db_session, monkeypatch):
    farmer = await _farmer(db_session)
    calls = []

    async def fake_upload(**kwargs):
        calls.append(kwargs)
        return {"object_path": f"farmer-documents/{kwargs['object_name']}.pdf", "size_bytes": len(kwargs["data"])}

    monkeypatch.setattr(storage, "upload_bytes", fake_upload)
    monkeypatch.setattr(storage, "read_url", lambda path: f"https://signed.example/{path}?X-Amz-Expires=900" if path else None)

    resp = await client.post(f"{API}/documents/upload", headers=auth_headers(farmer),
                             files={"file": ("aadhaar.pdf", b"%PDF-1.4", "application/pdf")}, data={"doc_type": "aadhaar"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"]["url"].endswith("X-Amz-Expires=900")
    assert calls[0]["bucket_key"] == "documents" and calls[0]["max_size_mb"] == 5

    profile = (await client.get(f"{API}/profile", headers=auth_headers(farmer))).json()["data"]
    assert profile["aadhaar_url"].startswith("https://signed.example/farmer-documents/farmer_")

    await client.post(f"{API}/documents/upload", headers=auth_headers(farmer),
                      files={"file": ("me.png", b"\x89PNG", "image/png")}, data={"doc_type": "avatar"})
    assert calls[1]["bucket_key"] == "avatars" and calls[1]["max_size_mb"] == 2
    assert calls[1]["allowed_content_types"] == {"image/jpeg", "image/png"}


async def test_document_upload_rejects_unknown_type(client, fake_redis, db_session):
    farmer = await _farmer(db_session)
    resp = await client.post(f"{API}/documents/upload", headers=auth_headers(farmer),
                             files={"file": ("x.pdf", b"%PDF", "application/pdf")}, data={"doc_type": "passport"})
    assert resp.status_code == 422


# ── Dashboard & market ──────────────────────────────────────────────────
async def test_dashboard_aggregates_every_tile(client, fake_redis, db_session, monkeypatch):
    from app.services import weather_service

    async def no_weather(*a, **k):
        return None

    monkeypatch.setattr(weather_service, "current_weather", no_weather)
    farmer = await _farmer(db_session, farm_name="Kaveri Farm")
    today = date.today()
    db_session.add_all([
        Crop(farmer_id=farmer.id, crop_name="North Paddy", crop_type="Rice", acres=Decimal("4.2"),
             sowing_date=today - timedelta(days=60), harvest_date=today + timedelta(days=60)),
        Crop(farmer_id=farmer.id, crop_type="Cotton", acres=Decimal("6.8"), sowing_date=today - timedelta(days=16)),
        MarketRate(crop_type="Paddy", grade="A", price_per_kg=Decimal("22.00"), effective_date=today - timedelta(days=3)),
        MarketRate(crop_type="Paddy", grade="A", price_per_kg=Decimal("23.50"), effective_date=today),
    ])
    await db_session.commit()

    resp = await client.get(f"{API}/dashboard", headers=auth_headers(farmer))
    assert resp.status_code == 200, resp.text
    d = resp.json()["data"]
    assert d["farm_name"] == "Kaveri Farm"
    assert d["active_crops_count"] == 2 and d["total_acres"] == 11.0
    paddy = next(c for c in d["crops"] if c["crop_name"] == "North Paddy")
    assert paddy["stage_progress_percent"] == 50
    assert paddy["health_status"] == "Not inspected yet"
    rate = next(r for r in d["mandi_prices"] if r["crop_type"] == "Paddy")
    assert rate["price_per_qtl"] == 2350.0 and rate["change_percentage"] == 6.82
    assert d["weather"] is None


async def test_completed_inspection_sets_crop_health(client, fake_redis, db_session, super_admin, monkeypatch):
    from app.services import weather_service

    async def no_weather(*a, **k):
        return None

    monkeypatch.setattr(weather_service, "current_weather", no_weather)
    farmer = await _farmer(db_session)
    crop = Crop(farmer_id=farmer.id, crop_type="Rice", acres=Decimal("2"), sowing_date=date.today() - timedelta(days=20))
    db_session.add(crop)
    await db_session.flush()
    visit = FarmVisit(crop_id=crop.id, farmer_id=farmer.id, visit_month=1, scheduled_date=date.today())
    db_session.add(visit)
    await db_session.commit()

    done = await client.patch(f"/api/v1/farm-visits/{visit.id}/complete", headers=auth_headers(super_admin),
                              json={"verified_acres": 2, "report": "ok", "diagnosis": "Healthy",
                                    "recommendation": "Continue drip irrigation"})
    assert done.status_code == 200
    d = (await client.get(f"{API}/dashboard", headers=auth_headers(farmer))).json()["data"]
    assert d["crops"][0]["health_status"] == "Healthy"
    assert (await client.get(f"{API}/notifications/unread-count", headers=auth_headers(farmer))).json()["data"]["unread_count"] == 1
