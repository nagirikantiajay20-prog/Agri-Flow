"""
Single shared Redis client for every subsystem that needs one: rate
limiting, OTP storage, response caching, and live-update pub/sub.

`is_available()` is a short-circuit breaker. Without it, an unreachable
Redis makes every optional call (cache read, cache invalidate, event
publish) pay a full TCP connect timeout, which turns a degraded
dependency into a latency outage on paths that are supposed to survive
it. After a failure the breaker stays open for BREAKER_COOLDOWN_SECONDS
so at most one request per cooldown window pays that cost.
"""
from __future__ import annotations

import time

import redis.asyncio as redis

from app.core.config import settings

BREAKER_COOLDOWN_SECONDS = 15.0

_client: redis.Redis | None = None
_unavailable_until: float = 0.0


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    return _client


def is_available() -> bool:
    return time.monotonic() >= _unavailable_until


def mark_unavailable() -> None:
    global _unavailable_until
    _unavailable_until = time.monotonic() + BREAKER_COOLDOWN_SECONDS


def mark_available() -> None:
    global _unavailable_until
    _unavailable_until = 0.0


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def ping() -> bool:
    try:
        ok = bool(await get_redis().ping())
    except Exception:
        mark_unavailable()
        return False
    mark_available()
    return ok
