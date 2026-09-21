"""
Live operational updates over Redis pub/sub.

Dashboards and notification bells previously had to poll. Polling is what
makes an "operational dashboard" feel stale and what multiplies backend
load by the number of open tabs. Publishing deltas on Redis pub/sub lets
every API instance fan a single write out to whichever instances happen
to hold the affected clients' SSE connections, so this works behind a
load balancer without sticky sessions.

Server-Sent Events rather than WebSockets: the traffic here is strictly
server to client, SSE rides on ordinary HTTP (no proxy/ALB upgrade
configuration), browsers reconnect automatically, and Dart/Flutter
clients consume it with a plain streamed HTTP request.
"""
from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis import get_redis, is_available, mark_available, mark_unavailable
from app.models.enums import UserRole

logger = get_logger(__name__)

CHANNEL_PREFIX = "events"
TICKET_PREFIX = "sse:ticket"
TICKET_TTL_SECONDS = 30

EVENT_NOTIFICATION = "notification.created"
EVENT_DASHBOARD = "dashboard.changed"


def user_channel(user_id: uuid.UUID) -> str:
    return f"{CHANNEL_PREFIX}:user:{user_id}"


def role_channel(role: UserRole) -> str:
    return f"{CHANNEL_PREFIX}:role:{role.value}"


def _default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, uuid.UUID):
        return str(obj)
    return str(obj)


async def _publish(channel: str, event: str, payload: dict) -> None:
    if not settings.LIVE_UPDATES_ENABLED or not is_available():
        return
    try:
        await get_redis().publish(
            channel, json.dumps({"event": event, "data": payload}, default=_default)
        )
        mark_available()
    except Exception as exc:
        mark_unavailable()
        logger.warning("event_publish_failed", channel=channel, event_name=event, error=str(exc))


async def publish_to_user(user_id: uuid.UUID, event: str, payload: dict) -> None:
    await _publish(user_channel(user_id), event, payload)


async def publish_to_roles(roles: list[UserRole], event: str, payload: dict) -> None:
    for role in roles:
        await _publish(role_channel(role), event, payload)


async def publish_dashboard_changed(reason: str) -> None:
    """Tells every open operational dashboard that its numbers moved, so
    it can refetch instead of polling on a timer."""
    await publish_to_roles(
        [UserRole.MANAGER, UserRole.SUPER_ADMIN], EVENT_DASHBOARD, {"reason": reason}
    )


async def issue_ticket(user_id: uuid.UUID) -> str:
    """Single-use, 30-second handshake token for EventSource, which cannot
    send an Authorization header. Keeps the bearer token out of the URL
    (and therefore out of proxy logs and browser history)."""
    ticket = uuid.uuid4().hex
    await get_redis().set(f"{TICKET_PREFIX}:{ticket}", str(user_id), ex=TICKET_TTL_SECONDS)
    return ticket


async def redeem_ticket(ticket: str) -> uuid.UUID | None:
    redis = get_redis()
    redis_key = f"{TICKET_PREFIX}:{ticket}"
    user_id = await redis.get(redis_key)
    if user_id is None:
        return None
    await redis.delete(redis_key)
    try:
        return uuid.UUID(user_id)
    except ValueError:
        return None
