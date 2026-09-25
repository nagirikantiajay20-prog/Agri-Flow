"""
Password hashing (Argon2id) and JWT access/refresh token issuance.

Replaces Supabase Auth (Master Plan §9 Phase 3 / Module 1). Access tokens
are short-lived; refresh tokens are longer-lived, opaque, hashed at rest,
and rotated on every use (see app.services.auth_service).

Dual hash-scheme verification (gap-fix #4 / Master Plan §6, §9 Phase 3-4):
accounts migrated from the legacy Supabase Auth system via
scripts/migrate_legacy_identity.py carry over their ORIGINAL bcrypt hash
from `auth.users.encrypted_password` rather than forcing every farmer to
reset their password on cutover day. `verify_password` detects the hash
scheme and verifies against the matching algorithm; `needs_rehash` tells
the caller (app.services.auth_service.authenticate) when a successful
bcrypt verification should trigger a lazy re-hash to Argon2id, so accounts
migrate to the stronger scheme transparently on their next successful
login rather than needing a bulk offline rehash (which is impossible
anyway — bcrypt hashes can't be converted without the plaintext password).
"""
import asyncio
import concurrent.futures
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import bcrypt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from jose import JWTError, jwt

from app.core.config import settings

_hasher = PasswordHasher()
_BCRYPT_PATTERN = re.compile(r"^\$2[aby]\$")

TokenType = Literal["access", "refresh"]

# ── Bounded worker pool for CPU-bound Argon2id operations ────────────────
# Bounds concurrency to prevent OS thread thrashing and memory exhaustion.
_MAX_PASSWORD_WORKERS = min(4, max(2, (os.cpu_count() or 2)))
_password_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=_MAX_PASSWORD_WORKERS,
    thread_name_prefix="argon2_worker",
)
_password_semaphore: asyncio.Semaphore | None = None


def _get_password_semaphore() -> asyncio.Semaphore:
    global _password_semaphore
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.Semaphore(_MAX_PASSWORD_WORKERS * 2)

    if _password_semaphore is None or getattr(_password_semaphore, "_loop", None) not in (None, loop):
        _password_semaphore = asyncio.Semaphore(_MAX_PASSWORD_WORKERS * 2)
    return _password_semaphore



# ── Password hashing ────────────────────────────────────────────────────
def hash_password(plain_password: str) -> str:
    """Always hashes NEW passwords with Argon2id — bcrypt is only ever
    verified against (for migrated legacy accounts), never produced."""
    return _hasher.hash(plain_password)


def is_bcrypt_hash(password_hash: str) -> bool:
    return bool(_BCRYPT_PATTERN.match(password_hash or ""))


def verify_password(plain_password: str, password_hash: str) -> bool:
    if is_bcrypt_hash(password_hash):
        try:
            return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))
        except (ValueError, TypeError):
            return False
    try:
        return _hasher.verify(password_hash, plain_password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


async def verify_password_async(plain_password: str, password_hash: str, *, timeout: float = 10.0) -> bool:
    """Non-blocking password verification executed on a dedicated, bounded ThreadPoolExecutor.

    Prevents CPU-heavy Argon2id computations from blocking the FastAPI event loop,
    ensuring that other concurrent requests (dashboard, seed catalog, health checks)
    maintain low latency and high responsiveness.
    """
    sem = _get_password_semaphore()
    loop = asyncio.get_running_loop()
    async with sem:
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(_password_executor, verify_password, plain_password, password_hash),
                timeout=timeout,
            )
        except (asyncio.TimeoutError, TimeoutError):
            return False


async def hash_password_async(plain_password: str, *, timeout: float = 10.0) -> str:
    """Non-blocking password hashing executed on a dedicated, bounded ThreadPoolExecutor."""
    sem = _get_password_semaphore()
    loop = asyncio.get_running_loop()
    async with sem:
        return await asyncio.wait_for(
            loop.run_in_executor(_password_executor, hash_password, plain_password),
            timeout=timeout,
        )


def needs_rehash(password_hash: str) -> bool:
    """True for any hash that isn't the current Argon2id scheme —
    currently just bcrypt (legacy-migrated accounts), but written as a
    scheme check rather than `is_bcrypt_hash` so a future Argon2
    parameter change (via PasswordHasher's own check_needs_rehash) is
    also covered."""
    if is_bcrypt_hash(password_hash):
        return True
    try:
        return _hasher.check_needs_rehash(password_hash)
    except Exception:
        return False


# ── JWT access tokens ────────────────────────────────────────────────────
def create_access_token(*, user_id: uuid.UUID, role: str, status: str) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "status": status,
        "type": "access",
        "iat": now,
        "exp": expire,
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    """Raises jose.JWTError if invalid/expired — callers translate to 401."""
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


def generate_opaque_refresh_token() -> str:
    """Refresh tokens are random opaque strings, not JWTs — only their hash
    is stored server-side (app.services.auth_service), so a leaked DB dump
    alone can't be replayed."""
    return secrets.token_urlsafe(48)


__all__ = [
    "hash_password",
    "hash_password_async",
    "verify_password",
    "verify_password_async",
    "is_bcrypt_hash",
    "needs_rehash",
    "create_access_token",
    "decode_token",
    "generate_opaque_refresh_token",
    "JWTError",
]

