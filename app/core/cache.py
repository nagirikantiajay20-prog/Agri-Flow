"""
Redis response cache for read-heavy endpoints.

Scope is deliberately narrow: only endpoints whose result is identical
for every caller in the same role (the public landing-page queries) or
whose staleness budget is larger than its refresh interval (operational
dashboards, which also receive live deltas over SSE). Per-farmer data is
never cached here, so a cache bug can never leak one farmer's rows to
another.

Every operation degrades to a direct call if Redis is unreachable — a
cache outage must slow the API down, not take it down.
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any, TypeVar

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis import get_redis, is_available, mark_available, mark_unavailable

logger = get_logger(__name__)

T = TypeVar("T")

PUBLIC_NAMESPACE = "public"
DASHBOARD_NAMESPACE = "dashboard"


def _default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"Not JSON serializable: {type(obj)!r}")


def key(namespace: str, *parts: str) -> str:
    return ":".join(("cache", namespace, *parts))


async def get_or_set(
    cache_key: str, ttl_seconds: int, loader: Callable[[], Awaitable[dict]]
) -> dict:
    if not settings.CACHE_ENABLED or not is_available():
        return await loader()

    try:
        redis = get_redis()
        hit = await redis.get(cache_key)
        mark_available()
        if hit is not None:
            return json.loads(hit)
    except Exception as exc:
        mark_unavailable()
        logger.warning("cache_read_failed", cache_key=cache_key, error=str(exc))
        return await loader()

    value = await loader()
    try:
        await redis.set(cache_key, json.dumps(value, default=_default), ex=ttl_seconds)
    except Exception as exc:
        mark_unavailable()
        logger.warning("cache_write_failed", cache_key=cache_key, error=str(exc))
    return value


async def invalidate(namespace: str) -> int:
    """Drops every entry in a namespace. Called from the services that
    mutate the underlying data so a dashboard or public page never shows
    a stale number after a write the user just made."""
    if not settings.CACHE_ENABLED or not is_available():
        return 0
    try:
        redis = get_redis()
        removed = 0
        async for k in redis.scan_iter(match=key(namespace, "*"), count=200):
            removed += await redis.delete(k)
        mark_available()
        return removed
    except Exception as exc:
        mark_unavailable()
        logger.warning("cache_invalidate_failed", namespace=namespace, error=str(exc))
        return 0
