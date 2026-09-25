"""
Auth + RBAC dependencies.

This is the single place that turns a bearer token into a `User` row —
every "own resource" endpoint in every router MUST get the actor's
identity from `get_current_user` (i.e. from the verified JWT), never
from a client-supplied `farmer_id` / `managerId` in the request body or
query string. This directly fixes the IDOR gap verified in
agriflow-web/supabase/functions/farmer-api (Master Plan §1.5 / §5).
"""
import uuid
from typing import Annotated

from fastapi import Depends, Query
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import decode_token
from app.models.enums import UserRole, UserStatus
from app.models.user import User
from app.schemas.common import PageParams

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

# ── Permission table (Master Plan §5) — the canonical source of truth for
# what each role may do. Endpoints declare a permission via
# `Depends(require_permission("crop.create"))`; nothing checks `role ==
# "..."` inline. Note: the legacy frontend's 4th `admin` role reference
# (Master Plan §1.6) is deliberately absent — it never matched a real DB
# row and is not carried forward.
ROLE_PERMISSIONS: dict[UserRole, set[str]] = {
    UserRole.FARMER: {
        "crop.read", "crop.create",
        "seed.read", "seed.purchase",
        "grain_sale.create", "grain_sale.read",
        "booking.create", "booking.read", "booking.cancel.own",
        "warehouse.read",
        "transaction.read",
        "visit.read",
        "market_rate.read",
        "profile.read.own", "profile.update.own",
        "notification.read.own",
        "bank_change.request",
        "dashboard.farmer.read",
        "document.upload.own",
        "device.register.own",
    },
    UserRole.MANAGER: {
        "farmer.read", "farmer.create", "farmer.approve",
        "crop.read",
        "visit.read", "visit.create", "visit.update",
        "seed.read", "seed_purchase.review",
        "grain_sale.read", "grain_sale.review", "grain_sale.inspect", "grain_sale.pay",
        "booking.read", "booking.review",
        "warehouse.read", "warehouse.manage",
        "procurement.manage",
        "market_rate.read",
        "transaction.read", "transaction.pay",
        "notification.read.own",
        "dashboard.admin.read",
        "report.read",
    },
    UserRole.SUPER_ADMIN: {"*"},
}

SUPER_ADMIN_ONLY_PERMISSIONS: frozenset[str] = frozenset({
    "manager.manage",
    "audit.read",
    "seed.manage",
    "market_rate.manage",
    "bank_change.review",
    "warehouse.create",
})

ALL_PERMISSIONS: frozenset[str] = frozenset(
    set().union(*(p for r, p in ROLE_PERMISSIONS.items() if "*" not in p)) | SUPER_ADMIN_ONLY_PERMISSIONS
)


def role_has_permission(role: UserRole, permission: str) -> bool:
    granted = ROLE_PERMISSIONS.get(role, set())
    return "*" in granted or permission in granted


async def get_current_user(
    token: Annotated[str | None, Depends(oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    if not token:
        raise UnauthorizedError("Missing bearer token")
    try:
        payload = decode_token(token)
    except JWTError as exc:
        raise UnauthorizedError("Invalid or expired token") from exc

    if payload.get("type") != "access":
        raise UnauthorizedError("Not an access token")

    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise UnauthorizedError("Malformed token subject") from exc

    user = await db.get(User, user_id)
    if user is None:
        raise UnauthorizedError("User no longer exists")
    if user.status == UserStatus.SUSPENDED:
        raise ForbiddenError("Account is suspended")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_active_user(user: CurrentUser) -> User:
    if user.status != UserStatus.ACTIVE:
        raise ForbiddenError("Account is not active yet — awaiting approval")
    return user


ActiveUser = Annotated[User, Depends(require_active_user)]


def _assert_known(*permissions: str) -> None:
    unknown = [p for p in permissions if p not in ALL_PERMISSIONS]
    if unknown:
        raise RuntimeError(f"Unknown permission(s) declared on a route: {unknown}")


def require_permission(permission: str):
    """Dependency factory — `Depends(require_permission('crop.create'))`."""
    _assert_known(permission)

    async def _check(user: ActiveUser) -> User:
        if not role_has_permission(user.role, permission):
            raise ForbiddenError(f"Missing permission: {permission}")
        return user

    return _check


def require_any_permission(*permissions: str):
    """Like require_permission but passes if the user holds ANY of the
    given permissions — e.g. a booking status change is reachable by a
    manager doing a full review (`booking.review`) OR a farmer cancelling
    their own booking (`booking.cancel.own`); which one applies, and the
    ownership check for the farmer case, is enforced in the service
    layer (app.services.booking_service.update_booking_status)."""

    _assert_known(*permissions)

    async def _check(user: ActiveUser) -> User:
        if not any(role_has_permission(user.role, p) for p in permissions):
            raise ForbiddenError(f"Missing permission: one of {list(permissions)}")
        return user

    return _check


def require_farmer(permission: str):
    """Guard for the farmer mobile API. Every /farmer route is scoped to
    "my own data", so a staff token is refused outright rather than being
    allowed through and silently returning the wrong scope — and the
    permission table still has the final say on what a farmer may do."""
    _assert_known(permission)

    async def _check(user: ActiveUser) -> User:
        if user.role != UserRole.FARMER:
            raise ForbiddenError("This endpoint is only available to farmer accounts")
        if not role_has_permission(user.role, permission):
            raise ForbiddenError(f"Missing permission: {permission}")
        return user

    return _check


def require_role(*roles: UserRole):
    """For the few endpoints keyed on role rather than a fine-grained
    permission (e.g. password reset is super_admin-only, mirroring the
    verified correct check in admin-create-user — Master Plan Module
    1/11)."""

    async def _check(user: ActiveUser) -> User:
        if user.role not in roles:
            raise ForbiddenError(f"Requires one of roles: {[r.value for r in roles]}")
        return user

    return _check


async def get_page_params(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None),
    status_: str | None = Query(None, alias="status"),
    sort: str = Query("created_at"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
) -> PageParams:
    return PageParams(page=page, page_size=page_size, search=search, status=status_, sort=sort, order=order)


Pagination = Annotated[PageParams, Depends(get_page_params)]
