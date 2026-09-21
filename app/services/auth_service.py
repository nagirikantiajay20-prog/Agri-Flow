"""
Auth service (Master Plan Module 1 — replaces auth-api's 5 actions).

Deliberately does NOT port the email/phone dual-lookup reconciliation
logic verified in the legacy auth-api's getUserData — that logic only
existed because of the old dual profiles/users identity model (Master
Plan §6). With the canonical `users` table as the single identity
source, lookups are always by primary key or a single unique column.
"""
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ConflictError, UnauthorizedError, ValidationError
from app.core.security import (
    create_access_token,
    generate_opaque_refresh_token,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.models.enums import UserRole, UserStatus
from app.models.user import FarmerProfile, RefreshToken, User
from app.services import audit_service, otp_service


def _hash_refresh_token(token: str) -> str:
    # Refresh tokens are opaque random strings (app.core.security);
    # store only a SHA-256 hash so a DB dump alone can't be replayed.
    return hashlib.sha256(token.encode()).hexdigest()


async def register_farmer(db: AsyncSession, *, name: str, phone: str, email: str | None, password: str) -> User:
    existing = await db.execute(select(User).where(User.phone == phone))
    if existing.scalar_one_or_none() is not None:
        raise ConflictError("An account with this phone number already exists")

    if email:
        existing_email = await db.execute(select(User).where(User.email == email))
        if existing_email.scalar_one_or_none() is not None:
            raise ConflictError("An account with this email already exists")

    user = User(
        name=name,
        phone=phone,
        email=email,
        password_hash=hash_password(password),
        role=UserRole.FARMER,
        status=UserStatus.PENDING,  # awaits manager/admin approval — Module 4
        first_login=True,
    )
    db.add(user)
    await db.flush()

    db.add(FarmerProfile(user_id=user.id, acres_of_land=Decimal("0")))
    await audit_service.record(db, actor_id=user.id, action="user.register", entity_type="user", entity_id=user.id)
    await db.flush()
    return user


async def authenticate(db: AsyncSession, *, phone: str, password: str) -> User:
    result = await db.execute(select(User).where(User.phone == phone))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        raise UnauthorizedError("Invalid phone number or password")
    if user.status == UserStatus.SUSPENDED:
        raise UnauthorizedError("Account is suspended")
    if user.status == UserStatus.REJECTED:
        raise UnauthorizedError("Account application was rejected")

    # Lazy rehash (gap-fix #4): an account migrated from legacy Supabase
    # Auth carries its original bcrypt hash (scripts/migrate_legacy_identity.py)
    # so it keeps working without a forced password reset. On the first
    # successful login post-migration, transparently upgrade it to
    # Argon2id — this is the only point a bcrypt hash can ever be
    # converted, since it requires the plaintext password.
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        await db.flush()

    return user


async def issue_token_pair(db: AsyncSession, *, user: User) -> tuple[str, str, int]:
    access_token = create_access_token(user_id=user.id, role=user.role.value, status=user.status.value)
    refresh_plain = generate_opaque_refresh_token()
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=_hash_refresh_token(refresh_plain),
            expires_at=expires_at,
        )
    )
    await db.flush()
    return access_token, refresh_plain, settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60


async def rotate_refresh_token(db: AsyncSession, *, refresh_token: str) -> tuple[str, str, int, User]:
    """Verifies + rotates a refresh token. If a token is presented that
    was already rotated (i.e. `revoked=True` and it's being reused),
    the entire token family for that user is revoked — this detects
    refresh-token theft/replay rather than silently accepting it."""
    token_hash = _hash_refresh_token(refresh_token)
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    stored = result.scalar_one_or_none()

    if stored is None:
        raise UnauthorizedError("Invalid refresh token")

    if stored.revoked:
        # Reuse of a rotated-away token — revoke every token for this user.
        await db.execute(
            RefreshToken.__table__.update()
            .where(RefreshToken.user_id == stored.user_id)
            .values(revoked=True)
        )
        raise UnauthorizedError("Refresh token reuse detected — all sessions revoked")

    if stored.expires_at < datetime.now(timezone.utc):
        raise UnauthorizedError("Refresh token expired")

    user = await db.get(User, stored.user_id)
    if user is None:
        raise UnauthorizedError("User no longer exists")

    new_access, new_refresh, expires_in = await issue_token_pair(db, user=user)
    stored.revoked = True
    stored.replaced_by_token_hash = _hash_refresh_token(new_refresh)
    await db.flush()
    return new_access, new_refresh, expires_in, user


async def revoke_all_sessions(db: AsyncSession, *, user_id: uuid.UUID) -> None:
    await db.execute(
        RefreshToken.__table__.update().where(RefreshToken.user_id == user_id).values(revoked=True)
    )
    await db.flush()


async def change_password(
    db: AsyncSession, *, user: User, current_password: str, new_password: str
) -> None:
    if not verify_password(current_password, user.password_hash):
        raise UnauthorizedError("Current password is incorrect")
    if current_password == new_password:
        raise ValidationError("The new password must differ from the current one")

    user.password_hash = hash_password(new_password)
    user.first_login = False
    await revoke_all_sessions(db, user_id=user.id)
    await audit_service.record(
        db, actor_id=user.id, action="user.password_change", entity_type="user", entity_id=user.id
    )
    await db.flush()


async def start_password_reset(db: AsyncSession, *, phone: str) -> str | None:
    """Always behaves identically whether or not the phone is registered,
    so this endpoint cannot be used to enumerate accounts."""
    result = await db.execute(select(User).where(User.phone == phone))
    user = result.scalar_one_or_none()
    if user is None:
        return None
    return await otp_service.issue(phone=phone, purpose=otp_service.PURPOSE_PASSWORD_RESET)


async def complete_password_reset(db: AsyncSession, *, phone: str, code: str, new_password: str) -> None:
    await otp_service.require_valid(
        phone=phone, code=code, purpose=otp_service.PURPOSE_PASSWORD_RESET
    )
    result = await db.execute(select(User).where(User.phone == phone))
    user = result.scalar_one_or_none()
    if user is None:
        raise ValidationError("The OTP is invalid or has expired")

    user.password_hash = hash_password(new_password)
    await revoke_all_sessions(db, user_id=user.id)
    await audit_service.record(
        db, actor_id=user.id, action="user.password_reset", entity_type="user", entity_id=user.id
    )
    await db.flush()
