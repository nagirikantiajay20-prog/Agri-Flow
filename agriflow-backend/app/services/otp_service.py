"""
Redis-backed OTP issuance and verification.

Replaces the legacy in-memory `server/utils/otp.js` Map, which could not
work on the serverless deployment targets the frontend is configured for
(Netlify Functions / Vercel) because each invocation got a fresh process,
and which returned the generated OTP in the HTTP response body.

Codes are stored only as a salted SHA-256 hash, expire after
OTP_TTL_SECONDS, and are invalidated after OTP_MAX_ATTEMPTS failures so a
6-digit code cannot be brute-forced within its lifetime.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

from app.core.config import settings
from app.core.exceptions import ValidationError
from app.core.logging import get_logger
from app.core.redis import get_redis

logger = get_logger(__name__)

OTP_TTL_SECONDS = 600
OTP_MAX_ATTEMPTS = 5
OTP_DIGITS = 6

PURPOSE_REGISTRATION = "registration"
PURPOSE_PASSWORD_RESET = "password_reset"
VALID_PURPOSES = frozenset({PURPOSE_REGISTRATION, PURPOSE_PASSWORD_RESET})


def _key(purpose: str, phone: str) -> str:
    return f"otp:{purpose}:{phone}"


def _hash(code: str, phone: str) -> str:
    return hmac.new(
        settings.JWT_SECRET_KEY.encode(),
        f"{phone}:{code}".encode(),
        hashlib.sha256,
    ).hexdigest()


def _generate_code() -> str:
    return f"{secrets.randbelow(10 ** OTP_DIGITS):0{OTP_DIGITS}d}"


async def issue(*, phone: str, purpose: str) -> str | None:
    if purpose not in VALID_PURPOSES:
        raise ValidationError(f"Unknown OTP purpose: {purpose}")

    code = _generate_code()
    redis = get_redis()
    key = _key(purpose, phone)
    await redis.delete(key)
    await redis.hset(key, mapping={"hash": _hash(code, phone), "attempts": "0"})
    await redis.expire(key, OTP_TTL_SECONDS)

    await deliver(phone=phone, code=code, purpose=purpose)
    return code if settings.OTP_ECHO_IN_RESPONSE else None


async def deliver(*, phone: str, code: str, purpose: str) -> None:
    """Delivery adapter. No SMS provider is configured in this project yet
    (the legacy backend also only logged), so development logs the code
    and production refuses to silently drop it."""
    if settings.ENVIRONMENT == "production":
        raise ValidationError(
            "No SMS provider is configured — set one up before enabling OTP flows in production"
        )
    logger.info("otp_issued", phone=phone, purpose=purpose, code=code)


async def verify(*, phone: str, code: str, purpose: str, consume: bool = True) -> bool:
    redis = get_redis()
    key = _key(purpose, phone)
    stored = await redis.hgetall(key)
    if not stored:
        return False

    attempts = int(stored.get("attempts", "0"))
    if attempts >= OTP_MAX_ATTEMPTS:
        await redis.delete(key)
        return False

    if not hmac.compare_digest(stored.get("hash", ""), _hash(code, phone)):
        await redis.hincrby(key, "attempts", 1)
        return False

    if consume:
        await redis.delete(key)
    return True


async def require_valid(*, phone: str, code: str, purpose: str, consume: bool = True) -> None:
    if not await verify(phone=phone, code=code, purpose=purpose, consume=consume):
        raise ValidationError("The OTP is invalid or has expired")
