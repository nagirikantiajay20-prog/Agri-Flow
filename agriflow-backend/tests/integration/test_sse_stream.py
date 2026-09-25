"""
SSE stream framing and handshake.

The generator is driven directly rather than through an HTTP client:
httpx's in-process ASGI transport buffers a response body, so it cannot
consume an endless stream. Live transport behaviour (status,
content-type, incremental delivery of `: connected` and `: keepalive`
frames) is verified against a real uvicorn server instead — see
docs/BACKEND_STATUS.md §7.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.api.v1.events import _event_stream, _resolve_subscriber
from app.core.exceptions import UnauthorizedError
from app.services import event_service

pytestmark = pytest.mark.asyncio


class _FakeRequest:
    """Stands in for starlette.Request: the generator only ever asks it
    whether the client has gone away."""

    def __init__(self, disconnect_after: int = 100):
        self._calls = 0
        self._disconnect_after = disconnect_after

    async def is_disconnected(self) -> bool:
        self._calls += 1
        return self._calls > self._disconnect_after


async def _drain(stream, stop_after_data: int = 1, timeout: float = 5.0) -> list[str]:
    frames: list[str] = []
    seen_data = 0

    async def run():
        nonlocal seen_data
        async for chunk in stream:
            frames.append(chunk)
            if chunk.startswith("data:"):
                seen_data += 1
                if seen_data >= stop_after_data:
                    return

    try:
        await asyncio.wait_for(run(), timeout=timeout)
    except TimeoutError:
        pass
    return frames


async def test_stream_opens_with_a_connected_comment(fake_redis, active_farmer):
    request = _FakeRequest(disconnect_after=0)
    frames = await _drain(
        _event_stream(request, [event_service.user_channel(active_farmer.id)]),
        stop_after_data=0,
        timeout=3.0,
    )
    assert frames and frames[0] == ": connected\n\n"


async def test_stream_formats_a_published_event_as_sse(fake_redis, active_farmer):
    channel = event_service.user_channel(active_farmer.id)
    stream = _event_stream(_FakeRequest(), [channel])

    first = await stream.__anext__()
    assert first == ": connected\n\n"

    async def publish():
        await asyncio.sleep(0.2)
        await event_service.publish_to_user(
            active_farmer.id,
            event_service.EVENT_NOTIFICATION,
            {"id": "abc", "title": "Slot confirmed"},
        )

    publisher = asyncio.create_task(publish())
    frames = await _drain(stream, stop_after_data=1, timeout=8.0)
    await publisher
    await stream.aclose()

    body = "".join(frames)
    assert "event: notification.created\n" in body, body
    data_line = next(f for f in frames if f.startswith("data:"))
    payload = json.loads(data_line[len("data:"):].strip())
    assert payload["title"] == "Slot confirmed"


async def test_stream_stops_when_the_client_disconnects(fake_redis, active_farmer):
    stream = _event_stream(
        _FakeRequest(disconnect_after=1), [event_service.user_channel(active_farmer.id)]
    )
    frames = [chunk async for chunk in stream]
    assert frames == [": connected\n\n"], "The generator must end once the client is gone"


async def test_resolve_subscriber_rejects_an_unknown_ticket(fake_redis):
    with pytest.raises(UnauthorizedError):
        await _resolve_subscriber("not-a-real-ticket")


async def test_resolve_subscriber_accepts_a_fresh_ticket_once(fake_redis, active_farmer):
    ticket = await event_service.issue_ticket(active_farmer.id)

    user_id, role = await _resolve_subscriber(ticket)
    assert user_id == active_farmer.id
    assert role == active_farmer.role

    with pytest.raises(UnauthorizedError):
        await _resolve_subscriber(ticket)


async def test_resolve_subscriber_rejects_a_suspended_account(fake_redis, suspended_farmer):
    ticket = await event_service.issue_ticket(suspended_farmer.id)
    with pytest.raises(UnauthorizedError):
        await _resolve_subscriber(ticket)
