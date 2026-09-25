"""
RBAC integrity verification.

Proves the stated security property directly: a restricted role cannot
reach a higher-tier operational view or an administrative endpoint. Every
route the application exposes is listed in ENDPOINTS below together with
the roles allowed to call it, and the suite then drives real HTTP
requests with real signed JWTs for each role and asserts the outcome.

Two properties are checked, and the second is what stops this file
rotting:

1. Denied combinations return exactly 403 (or 401 for anonymous) — never
   200, and never a 404/422 that would imply the handler ran.
2. test_every_route_is_covered fails if a route exists that this matrix
   does not mention, so a newly added endpoint cannot ship without an
   explicit, reviewed decision about who may call it.

Allowed combinations assert only "not denied": the request may still fail
on a nonexistent UUID or an unsatisfiable body, which is fine — the point
is that authorization let it through to the handler.
"""
import uuid

import pytest

from app.core.dependencies import ROLE_PERMISSIONS, role_has_permission
from app.models.enums import UserRole
from tests.utils import auth_headers

pytestmark = pytest.mark.asyncio

FARMER = "farmer"
MANAGER = "manager"
SUPER_ADMIN = "super_admin"
ANON = "anonymous"

ALL_ROLES = (FARMER, MANAGER, SUPER_ADMIN)

_ID = str(uuid.UUID(int=0))

# (method, path, {roles allowed}, optional json body)
ENDPOINTS: list[tuple[str, str, set[str], dict | None]] = [
    # ── Public ────────────────────────────────────────────────────────
    ("GET", "/api/v1/public/market-rates", {ANON, *ALL_ROLES}, None),
    ("GET", "/api/v1/public/seeds", {ANON, *ALL_ROLES}, None),
    ("GET", "/api/v1/public/stats", {ANON, *ALL_ROLES}, None),
    ("POST", "/api/v1/auth/login", {ANON, *ALL_ROLES}, {"phone": "0", "password": "x"}),
    ("POST", "/api/v1/auth/register", {ANON, *ALL_ROLES}, None),
    ("POST", "/api/v1/auth/refresh", {ANON, *ALL_ROLES}, {"refresh_token": "x"}),
    ("POST", "/api/v1/auth/send-otp", {ANON, *ALL_ROLES}, {"phone": "9990001111"}),
    ("POST", "/api/v1/auth/verify-otp", {ANON, *ALL_ROLES}, {"phone": "9990001111", "code": "000000"}),
    ("POST", "/api/v1/auth/forgot-password/send-otp", {ANON, *ALL_ROLES}, {"phone": "9990001111"}),
    (
        "POST",
        "/api/v1/auth/forgot-password/reset",
        {ANON, *ALL_ROLES},
        {"phone": "9990001111", "code": "000000", "new_password": "newpass123"},
    ),
    # ── Authenticated, any role ───────────────────────────────────────
    ("POST", "/api/v1/auth/logout", set(ALL_ROLES), None),
    ("GET", "/api/v1/users/me", set(ALL_ROLES), None),
    ("PATCH", "/api/v1/users/me", set(ALL_ROLES), {"name": "New Name"}),
    (
        "POST",
        "/api/v1/auth/change-password",
        set(ALL_ROLES),
        {"current_password": "testpass123", "new_password": "anotherpass123"},
    ),
    ("GET", "/api/v1/notifications", set(ALL_ROLES), None),
    ("PATCH", "/api/v1/notifications/read-all", set(ALL_ROLES), None),
    ("PATCH", f"/api/v1/notifications/{_ID}/read", set(ALL_ROLES), None),
    ("POST", "/api/v1/events/ticket", set(ALL_ROLES), None),
    ("POST", "/api/v1/uploads/presign", set(ALL_ROLES), None),
    ("POST", "/api/v1/uploads", set(ALL_ROLES), None),
    # ── Farmer-owned resources ────────────────────────────────────────
    ("GET", "/api/v1/farmers/me", {FARMER, SUPER_ADMIN}, None),
    ("PATCH", "/api/v1/farmers/me", {FARMER, SUPER_ADMIN}, {"address": "x"}),
    ("GET", "/api/v1/farmers/me/dashboard", {FARMER, SUPER_ADMIN}, None),
    (
        "POST",
        "/api/v1/farmers/me/bank-change-requests",
        {FARMER, SUPER_ADMIN},
        {"bank_name": "Bank", "account_number": "1234", "ifsc_code": "IFSC0001"},
    ),
    ("GET", "/api/v1/crops", {FARMER, MANAGER, SUPER_ADMIN}, None),
    (
        "POST",
        "/api/v1/crops",
        {FARMER, SUPER_ADMIN},
        {"crop_type": "Rice", "acres": "1", "sowing_date": "2026-01-01"},
    ),
    ("GET", "/api/v1/seeds", set(ALL_ROLES), None),
    ("GET", "/api/v1/seed-purchases", set(ALL_ROLES), None),
    ("POST", "/api/v1/seed-purchases", {FARMER, SUPER_ADMIN}, {"seed_id": _ID, "quantity_kg": "1"}),
    ("GET", "/api/v1/grain-sales", set(ALL_ROLES), None),
    (
        "POST",
        "/api/v1/grain-sales",
        {FARMER, SUPER_ADMIN},
        {"grain_type": "Rice", "grade": "A", "raw_material_kg": "10"},
    ),
    ("GET", "/api/v1/bookings", set(ALL_ROLES), None),
    (
        "POST",
        "/api/v1/bookings",
        {FARMER, SUPER_ADMIN},
        {
            "warehouse_id": _ID,
            "warehouse_slot_id": _ID,
            "booking_date": "2026-01-01",
            "delivery_address": "addr",
            "grain_type": "Rice",
            "quantity_kg": "1",
        },
    ),
    ("PATCH", f"/api/v1/bookings/{_ID}/status", set(ALL_ROLES), {"status": "cancelled"}),
    ("GET", "/api/v1/transactions", set(ALL_ROLES), None),
    ("GET", "/api/v1/warehouses", set(ALL_ROLES), None),
    ("GET", f"/api/v1/warehouses/{_ID}/slots", set(ALL_ROLES), None),
    ("GET", "/api/v1/warehouse-slots", set(ALL_ROLES), None),
    ("GET", "/api/v1/farm-visits", set(ALL_ROLES), None),
    ("GET", "/api/v1/market-rates", set(ALL_ROLES), None),
    # ── Manager tier: farmers must NOT reach these ────────────────────
    ("GET", "/api/v1/farmers", {MANAGER, SUPER_ADMIN}, None),
    ("GET", f"/api/v1/farmers/{_ID}", {MANAGER, SUPER_ADMIN}, None),
    (
        "POST",
        "/api/v1/farmers",
        {MANAGER, SUPER_ADMIN},
        {"name": "New Farmer", "phone": "9998887777", "password": "testpass123"},
    ),
    ("PATCH", f"/api/v1/farmers/{_ID}/approval", {MANAGER, SUPER_ADMIN}, {"status": "active"}),
    ("PATCH", f"/api/v1/farmers/{_ID}", {MANAGER, SUPER_ADMIN}, {"name": "Updated Farmer"}),
    ("GET", "/api/v1/crops/active", {FARMER, MANAGER, SUPER_ADMIN}, None),
    (
        "POST",
        "/api/v1/farm-visits",
        {MANAGER, SUPER_ADMIN},
        {"crop_id": _ID, "visit_month": 1, "scheduled_date": "2026-01-01"},
    ),
    ("POST", "/api/v1/farm-visits/reminders", {MANAGER, SUPER_ADMIN}, None),
    (
        "PATCH",
        f"/api/v1/farm-visits/{_ID}/schedule",
        {MANAGER, SUPER_ADMIN},
        {"scheduled_date": "2026-01-01"},
    ),
    (
        "PATCH",
        f"/api/v1/farm-visits/{_ID}/complete",
        {MANAGER, SUPER_ADMIN},
        {"verified_acres": "1", "report": "ok"},
    ),
    (
        "PATCH",
        f"/api/v1/seed-purchases/{_ID}/status",
        {MANAGER, SUPER_ADMIN},
        {"payment_status": "paid"},
    ),
    (
        "PATCH",
        f"/api/v1/grain-sales/{_ID}/review",
        {MANAGER, SUPER_ADMIN},
        {"booking_slot_id": _ID, "good_quantity_kg": "1", "bad_quantity_kg": "0", "approve": True},
    ),
    ("PATCH", f"/api/v1/grain-sales/{_ID}/pay", {MANAGER, SUPER_ADMIN}, {}),
    (
        "POST",
        "/api/v1/grain-sales/procure",
        {MANAGER, SUPER_ADMIN},
        {
            "farmer_id": _ID,
            "grain_type": "Rice",
            "grade": "A",
            "raw_material_kg": "10",
            "good_material_kg": "9",
            "wastage_kg": "1",
        },
    ),
    (
        "PATCH",
        f"/api/v1/grain-sales/{_ID}/yield",
        {MANAGER, SUPER_ADMIN},
        {"good_material_kg": "9", "wastage_kg": "1"},
    ),
    (
        "POST",
        f"/api/v1/bookings/{_ID}/inspect",
        {MANAGER, SUPER_ADMIN},
        {"good_quantity_kg": "9", "bad_quantity_kg": "1"},
    ),
    ("PATCH", f"/api/v1/transactions/{_ID}/pay", {MANAGER, SUPER_ADMIN}, {}),
    ("GET", f"/api/v1/warehouses/{_ID}", {MANAGER, SUPER_ADMIN}, None),
    ("PATCH", f"/api/v1/warehouses/{_ID}", {MANAGER, SUPER_ADMIN}, {"name": "W-Updated"}),
    ("GET", f"/api/v1/warehouses/{_ID}/inventory", {MANAGER, SUPER_ADMIN}, None),
    (
        "POST",
        f"/api/v1/warehouses/{_ID}/inventory",
        {MANAGER, SUPER_ADMIN},
        {"grain_type": "Rice", "quantity_kg": "10"},
    ),
    (
        "POST",
        "/api/v1/warehouse-slots",
        {MANAGER, SUPER_ADMIN},
        {
            "warehouse_id": _ID,
            "slot_date": "2026-01-01",
            "start_time": "09:00:00",
            "end_time": "10:00:00",
            "capacity_kg": "100",
        },
    ),
    ("PATCH", f"/api/v1/warehouse-slots/{_ID}", {MANAGER, SUPER_ADMIN}, {"status": "cancelled"}),
    ("DELETE", f"/api/v1/warehouse-slots/{_ID}", {MANAGER, SUPER_ADMIN}, None),
    ("GET", "/api/v1/admin/dashboard", {MANAGER, SUPER_ADMIN}, None),
    ("GET", "/api/v1/admin/reports/monthly?month=2026-01", {MANAGER, SUPER_ADMIN}, None),
    # ── Super-admin tier: managers must NOT reach these ───────────────
    ("GET", "/api/v1/admin/audit-logs", {SUPER_ADMIN}, None),
    ("GET", "/api/v1/managers", {SUPER_ADMIN}, None),
    (
        "POST",
        "/api/v1/managers",
        {SUPER_ADMIN},
        {"name": "M", "phone": "9998887766", "password": "testpass123", "role": "manager"},
    ),
    ("PATCH", f"/api/v1/managers/{_ID}/status", {SUPER_ADMIN}, {"status": "suspended"}),
    ("PATCH", f"/api/v1/managers/{_ID}", {SUPER_ADMIN}, {"name": "M-Updated"}),
    (
        "POST",
        f"/api/v1/managers/{_ID}/reset-password",
        {SUPER_ADMIN},
        {"new_password": "brandnewpass123"},
    ),
    ("GET", "/api/v1/farmers/bank-change-requests", {SUPER_ADMIN}, None),
    (
        "PATCH",
        f"/api/v1/farmers/bank-change-requests/{_ID}",
        {SUPER_ADMIN},
        {"approve": True},
    ),
    ("POST", "/api/v1/seeds", {SUPER_ADMIN}, {"name": "S", "price_per_kg": "1", "stock_kg": "1"}),
    ("PATCH", f"/api/v1/seeds/{_ID}", {SUPER_ADMIN}, {"name": "S2"}),
    ("DELETE", f"/api/v1/seeds/{_ID}", {SUPER_ADMIN}, None),
    ("POST", "/api/v1/warehouses", {SUPER_ADMIN}, {"name": "W", "address": "addr", "total_capacity_kg": "100"}),
    ("DELETE", f"/api/v1/warehouses/{_ID}", {SUPER_ADMIN}, None),
    (
        "POST",
        "/api/v1/market-rates",
        {SUPER_ADMIN},
        {"crop_type": "Rice", "grade": "A", "price_per_kg": "20", "effective_date": "2026-01-01"},
    ),
    # ── Farmer mobile app: farmer-only, staff tokens refused ──────────
    (
        "POST",
        "/api/v1/farmer/auth/login",
        {ANON, *ALL_ROLES},
        {"phone": "9990001111", "password": "wrongpass1"},
    ),
    ("POST", "/api/v1/farmer/auth/refresh", {ANON, *ALL_ROLES}, {"refresh_token": "not-a-real-token"}),
    ("POST", "/api/v1/farmer/auth/logout", set(ALL_ROLES), None),
    ("GET", "/api/v1/farmer/profile", {FARMER}, None),
    ("PATCH", "/api/v1/farmer/profile", {FARMER}, {"village": "Kalluru"}),
    ("PUT", "/api/v1/farmer/profile", {FARMER}, {"village": "Kalluru"}),
    (
        "POST",
        "/api/v1/farmer/profile/bank-request",
        {FARMER},
        {"bank_name": "State Bank", "account_number": "10293847561", "ifsc_code": "SBIN0001234"},
    ),
    ("GET", "/api/v1/farmer/dashboard", {FARMER}, None),
    ("GET", "/api/v1/farmer/market-rates", {FARMER}, None),
    ("GET", "/api/v1/farmer/seeds", {FARMER}, None),
    ("POST", "/api/v1/farmer/seeds/purchase", {FARMER}, {"seed_id": _ID, "quantity_kg": "1"}),
    ("GET", "/api/v1/farmer/seeds/purchases", {FARMER}, None),
    ("GET", f"/api/v1/farmer/seeds/{_ID}", {FARMER}, None),
    ("GET", "/api/v1/farmer/crops", {FARMER}, None),
    (
        "POST",
        "/api/v1/farmer/crops",
        {FARMER},
        {"crop_type": "Rice", "acres": "1", "sowing_date": "2026-06-01"},
    ),
    ("GET", "/api/v1/farmer/crops/visits", {FARMER}, None),
    ("PATCH", f"/api/v1/farmer/crops/{_ID}", {FARMER}, {"status": "Growing"}),
    ("PUT", f"/api/v1/farmer/crops/{_ID}", {FARMER}, {"status": "Growing"}),
    ("DELETE", f"/api/v1/farmer/crops/{_ID}", {FARMER}, None),
    ("GET", f"/api/v1/farmer/crops/{_ID}/inspections", {FARMER}, None),
    ("POST", f"/api/v1/farmer/crops/{_ID}/scan", {FARMER}, None),
    ("GET", "/api/v1/farmer/warehouses", {FARMER}, None),
    ("GET", f"/api/v1/farmer/warehouses/{_ID}", {FARMER}, None),
    ("GET", f"/api/v1/farmer/warehouses/{_ID}/slots", {FARMER}, None),
    (
        "POST",
        "/api/v1/farmer/grain-sales/book-slot",
        {FARMER},
        {
            "warehouse_id": _ID,
            "warehouse_slot_id": _ID,
            "grain_type": "Rice",
            "quantity_kg": "1",
            "booking_date": "2026-01-01",
            "delivery_address": "addr",
        },
    ),
    ("GET", "/api/v1/farmer/grain-sales/bookings", {FARMER}, None),
    ("DELETE", f"/api/v1/farmer/grain-sales/bookings/{_ID}", {FARMER}, None),
    ("GET", "/api/v1/farmer/grain-sales/offers", {FARMER}, None),
    (
        "POST",
        "/api/v1/farmer/grain-sales/offers",
        {FARMER},
        {"crop_type": "Cotton", "grade": "A", "quantity_kg": "100"},
    ),
    ("GET", "/api/v1/farmer/notifications", {FARMER}, None),
    ("GET", "/api/v1/farmer/notifications/unread-count", {FARMER}, None),
    ("PATCH", f"/api/v1/farmer/notifications/{_ID}/read", {FARMER}, None),
    ("PUT", f"/api/v1/farmer/notifications/{_ID}/read", {FARMER}, None),
    ("POST", "/api/v1/farmer/notifications/read-all", {FARMER}, None),
    ("PUT", "/api/v1/farmer/notifications/read-all", {FARMER}, None),
    (
        "POST",
        "/api/v1/farmer/notifications/fcm-token",
        {FARMER},
        {"fcm_token": "fcm-token-for-rbac-matrix-test-0001", "device_type": "android"},
    ),
    ("POST", "/api/v1/farmer/notifications/test-fcm", {FARMER}, None),
    ("GET", "/api/v1/farmer/transactions", {FARMER}, None),
    ("POST", "/api/v1/farmer/documents/upload", {FARMER}, None),
]

# Routes that carry no RBAC decision of their own.
EXEMPT_ROUTES = {
    ("GET", "/health"),
    ("GET", "/health/live"),
    ("GET", "/health/ready"),
    ("GET", "/api/v1/events/stream"),
}


def _strip_query(path: str) -> str:
    return path.split("?", 1)[0]


def _template(path: str) -> str:
    """Turns a concrete matrix path back into its route template."""
    return _strip_query(path).replace(_ID, "{id}")


async def _call(client, method: str, path: str, headers: dict, body: dict | None):
    kwargs = {"headers": headers}
    if body is not None:
        kwargs["json"] = body
    return await client.request(method, path, **kwargs)


def _actor(role: str, farmer, mgr, admin):
    return {FARMER: farmer, MANAGER: mgr, SUPER_ADMIN: admin}[role]


@pytest.mark.parametrize(
    ("method", "path", "allowed", "body"),
    [(m, p, a, b) for (m, p, a, b) in ENDPOINTS],
    ids=[f"{m}:{p}" for (m, p, _a, _b) in ENDPOINTS],
)
async def test_restricted_roles_are_denied(
    client, fake_redis, active_farmer, manager, super_admin, method, path, allowed, body
):
    for role in ALL_ROLES:
        if role in allowed:
            continue
        user = _actor(role, active_farmer, manager, super_admin)
        resp = await _call(client, method, path, auth_headers(user), body)
        assert resp.status_code == 403, (
            f"{role} must be FORBIDDEN from {method} {path}, got {resp.status_code}: {resp.text[:200]}"
        )


@pytest.mark.parametrize(
    ("method", "path", "allowed", "body"),
    [(m, p, a, b) for (m, p, a, b) in ENDPOINTS],
    ids=[f"{m}:{p}" for (m, p, _a, _b) in ENDPOINTS],
)
async def test_permitted_roles_are_not_denied(
    client, fake_redis, active_farmer, manager, super_admin, method, path, allowed, body
):
    for role in ALL_ROLES:
        if role not in allowed:
            continue
        user = _actor(role, active_farmer, manager, super_admin)
        resp = await _call(client, method, path, auth_headers(user), body)
        # On credential endpoints a 401 means "those credentials are wrong",
        # not "you are not authorized", so only 403 counts as a denial there.
        rejected = (403,) if ANON in allowed else (401, 403)
        assert resp.status_code not in rejected, (
            f"{role} must be ALLOWED through to {method} {path}, got {resp.status_code}: {resp.text[:200]}"
        )


@pytest.mark.parametrize(
    ("method", "path", "allowed", "body"),
    [(m, p, a, b) for (m, p, a, b) in ENDPOINTS if ANON not in a],
    ids=[f"{m}:{p}" for (m, p, a, _b) in ENDPOINTS if ANON not in a],
)
async def test_anonymous_callers_are_rejected(client, fake_redis, method, path, allowed, body):
    resp = await _call(client, method, path, {}, body)
    assert resp.status_code in (401, 403), (
        f"anonymous must not reach {method} {path}, got {resp.status_code}: {resp.text[:200]}"
    )


async def test_every_route_is_covered():
    """A new endpoint cannot ship without an explicit RBAC decision."""
    import app.main as m

    declared = {(method, _template(path)) for (method, path, _a, _b) in ENDPOINTS}
    missing = []
    for path, operations in m.app.openapi()["paths"].items():
        template = path.replace("{farmer_id}", "{id}").replace("{manager_id}", "{id}")
        template = (
            template.replace("{request_id}", "{id}")
            .replace("{seed_id}", "{id}")
            .replace("{purchase_id}", "{id}")
            .replace("{sale_id}", "{id}")
            .replace("{booking_id}", "{id}")
            .replace("{visit_id}", "{id}")
            .replace("{notification_id}", "{id}")
            .replace("{warehouse_id}", "{id}")
            .replace("{slot_id}", "{id}")
            .replace("{transaction_id}", "{id}")
            .replace("{crop_id}", "{id}")
        )
        for method in operations:
            pair = (method.upper(), template)
            if pair in EXEMPT_ROUTES or pair in declared:
                continue
            missing.append(f"{method.upper()} {path}")

    assert not missing, (
        "These routes have no entry in the RBAC matrix — add one (and decide who may call them):\n  "
        + "\n  ".join(sorted(missing))
    )


async def test_pending_farmer_cannot_use_the_api(client, fake_redis, pending_farmer):
    """Approval gate: registering is not the same as being allowed in."""
    resp = await client.get("/api/v1/crops", headers=auth_headers(pending_farmer))
    assert resp.status_code == 403


async def test_suspended_farmer_cannot_use_the_api(client, fake_redis, suspended_farmer):
    resp = await client.get("/api/v1/crops", headers=auth_headers(suspended_farmer))
    assert resp.status_code == 403


async def test_farmer_token_cannot_be_escalated_by_claiming_another_role(client, fake_redis, active_farmer):
    """The role in the JWT is never trusted — authorization reads the role
    from the database row the token's subject points at."""
    from app.core.security import create_access_token

    forged = create_access_token(
        user_id=active_farmer.id, role=UserRole.SUPER_ADMIN.value, status="active"
    )
    resp = await client.get("/api/v1/managers", headers={"Authorization": f"Bearer {forged}"})
    assert resp.status_code == 403, "A role claim in the token must not grant privileges"


async def test_permission_table_has_no_privilege_creep():
    """Farmers hold no administrative permission, and managers hold none
    of the super-admin-only ones."""
    admin_only = {
        "manager.manage", "audit.read", "seed.manage",
        "market_rate.manage", "bank_change.review", "warehouse.create",
    }
    farmer_perms = ROLE_PERMISSIONS[UserRole.FARMER]
    manager_perms = ROLE_PERMISSIONS[UserRole.MANAGER]

    assert not (farmer_perms & admin_only)
    assert not (manager_perms & admin_only)
    assert not (farmer_perms & manager_perms & {"farmer.approve", "grain_sale.review", "booking.review"})

    for permission in admin_only:
        assert not role_has_permission(UserRole.FARMER, permission)
        assert not role_has_permission(UserRole.MANAGER, permission)
        assert role_has_permission(UserRole.SUPER_ADMIN, permission)
