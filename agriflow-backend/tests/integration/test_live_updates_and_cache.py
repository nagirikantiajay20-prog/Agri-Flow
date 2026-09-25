"""
Live operational updates and response caching.

Covers the "real-time operational dashboard updates" requirement: a write
made by one actor must reach a connected dashboard without polling, and
the dashboard cache must not be able to serve a number that a write has
already invalidated.
"""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import pytest

from app.core import cache
from app.core.config import settings
from app.models.enums import NotificationType, UserRole
from app.services import event_service, notification_service

pytestmark = pytest.mark.asyncio


async def _collect(pubsub, count: int, timeout: float = 3.0) -> list[dict]:
    received: list[dict] = []
    deadline = asyncio.get_running_loop().time() + timeout
    while len(received) < count and asyncio.get_running_loop().time() < deadline:
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
        if message is not None:
            received.append(json.loads(message["data"]))
    return received


async def test_notification_reaches_the_recipients_live_channel(fake_redis, db_session, active_farmer):
    pubsub = fake_redis.pubsub()
    await pubsub.subscribe(event_service.user_channel(active_farmer.id))

    await notification_service.notify_user(
        db_session,
        user_id=active_farmer.id,
        title="Booking confirmed",
        message="Your slot is confirmed",
        type_=NotificationType.SUCCESS,
    )

    events = await _collect(pubsub, 1)
    await pubsub.unsubscribe()
    await pubsub.aclose()

    assert events, "A new notification must be published to the recipient's live channel"
    assert events[0]["event"] == event_service.EVENT_NOTIFICATION
    assert events[0]["data"]["title"] == "Booking confirmed"


async def test_notification_is_not_published_to_other_users_channels(
    fake_redis, db_session, active_farmer, manager
):
    pubsub = fake_redis.pubsub()
    await pubsub.subscribe(event_service.user_channel(manager.id))

    await notification_service.notify_user(
        db_session, user_id=active_farmer.id, title="Private", message="Not yours"
    )

    events = await _collect(pubsub, 1, timeout=0.5)
    await pubsub.unsubscribe()
    await pubsub.aclose()

    assert not events, "A notification must never fan out to an unrelated user's channel"


async def test_write_announces_a_dashboard_change_to_operations_roles(
    fake_redis, db_session, active_farmer
):
    pubsub = fake_redis.pubsub()
    await pubsub.subscribe(event_service.role_channel(UserRole.SUPER_ADMIN))

    from app.services import audit_service

    await audit_service.record(
        db_session, actor_id=active_farmer.id, action="booking.create", entity_type="booking_slot"
    )

    events = await _collect(pubsub, 1)
    await pubsub.unsubscribe()
    await pubsub.aclose()

    assert events, "Operational dashboards must be told when the underlying numbers move"
    assert events[0]["event"] == event_service.EVENT_DASHBOARD
    assert events[0]["data"]["reason"] == "booking.create"


async def test_stream_ticket_round_trip_over_http(client, fake_redis, active_farmer):
    from tests.utils import auth_headers

    resp = await client.post("/api/v1/events/ticket", headers=auth_headers(active_farmer))
    assert resp.status_code == 200
    ticket = resp.json()["data"]["ticket"]
    assert await event_service.redeem_ticket(ticket) == active_farmer.id


async def test_cache_serves_a_second_read_without_recomputing(fake_redis):
    calls = {"n": 0}

    async def loader():
        calls["n"] += 1
        return {"value": calls["n"]}

    key = cache.key(cache.PUBLIC_NAMESPACE, "unit-test")
    first = await cache.get_or_set(key, 60, loader)
    second = await cache.get_or_set(key, 60, loader)

    assert first == second == {"value": 1}
    assert calls["n"] == 1, "The second read must come from cache"


async def test_cache_invalidation_forces_a_recompute(fake_redis):
    calls = {"n": 0}

    async def loader():
        calls["n"] += 1
        return {"value": calls["n"]}

    key = cache.key(cache.DASHBOARD_NAMESPACE, "unit-test")
    await cache.get_or_set(key, 60, loader)
    await cache.invalidate(cache.DASHBOARD_NAMESPACE)
    after = await cache.get_or_set(key, 60, loader)

    assert calls["n"] == 2
    assert after == {"value": 2}


async def test_cache_round_trips_decimal_money_values(fake_redis):
    async def loader():
        return {"total": Decimal("1234.56")}

    key = cache.key(cache.DASHBOARD_NAMESPACE, "decimal-test")
    await cache.get_or_set(key, 60, loader)
    cached = await cache.get_or_set(key, 60, loader)
    assert Decimal(cached["total"]) == Decimal("1234.56")


async def test_a_write_invalidates_the_admin_dashboard_cache(
    client, fake_redis, db_session, super_admin, active_farmer
):
    from tests.utils import auth_headers

    first = await client.get("/api/v1/admin/dashboard", headers=auth_headers(super_admin))
    assert first.status_code == 200
    assert await fake_redis.get(cache.key(cache.DASHBOARD_NAMESPACE, "admin")) is not None

    from app.services import audit_service

    await audit_service.record(
        db_session, actor_id=active_farmer.id, action="crop.register", entity_type="crop"
    )
    await db_session.commit()

    assert await fake_redis.get(cache.key(cache.DASHBOARD_NAMESPACE, "admin")) is None, (
        "A write must drop the cached dashboard so the next read is fresh"
    )


async def test_cache_is_bypassed_cleanly_when_disabled(fake_redis, monkeypatch):
    monkeypatch.setattr(settings, "CACHE_ENABLED", False)
    calls = {"n": 0}

    async def loader():
        calls["n"] += 1
        return {"value": calls["n"]}

    key = cache.key(cache.PUBLIC_NAMESPACE, "disabled-test")
    await cache.get_or_set(key, 60, loader)
    await cache.get_or_set(key, 60, loader)
    assert calls["n"] == 2


async def test_api_survives_redis_being_unreachable(client, monkeypatch):
    """A Redis outage must degrade the API, not take it down."""
    from app.core import redis as redis_module

    monkeypatch.setattr(redis_module, "is_available", lambda: False)
    resp = await client.get("/api/v1/public/stats")
    assert resp.status_code == 200
    assert "farmers" in resp.json()["data"]
