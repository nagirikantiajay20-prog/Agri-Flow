import asyncio
import json
import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Query, Request
from starlette.responses import StreamingResponse

from app.core.database import session_scope
from app.core.dependencies import ActiveUser
from app.core.exceptions import UnauthorizedError
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.models.enums import UserRole, UserStatus
from app.models.user import User
from app.schemas.common import SuccessResponse
from app.schemas.events import StreamTicketResponse
from app.services import event_service

logger = get_logger(__name__)

router = APIRouter(prefix="/events", tags=["events"])

HEARTBEAT_SECONDS = 20
POLL_SECONDS = 0.5


@router.post("/ticket", response_model=SuccessResponse[StreamTicketResponse])
async def create_stream_ticket(user: ActiveUser):
    ticket = await event_service.issue_ticket(user.id)
    return SuccessResponse(
        data=StreamTicketResponse(ticket=ticket, expires_in=event_service.TICKET_TTL_SECONDS)
    )


async def _resolve_subscriber(ticket: str) -> tuple[uuid.UUID, UserRole]:
    """Validates the ticket and returns who to subscribe.

    The database session is opened and closed here rather than injected
    with Depends(get_db): a dependency with yield stays open for the
    whole response, and an SSE response lasts as long as the browser tab.
    That would pin one pooled connection per connected dashboard and
    exhaust the pool well before the number of viewers got interesting.
    """
    user_id = await event_service.redeem_ticket(ticket)
    if user_id is None:
        raise UnauthorizedError("Invalid or expired stream ticket")

    async with session_scope() as db:
        user = await db.get(User, user_id)
        if user is None or user.status != UserStatus.ACTIVE:
            raise UnauthorizedError("Account is not active")
        return user.id, user.role


async def _event_stream(request: Request, channels: list[str]) -> AsyncGenerator[str, None]:
    pubsub = get_redis().pubsub()
    await pubsub.subscribe(*channels)
    try:
        yield ": connected\n\n"
        idle = 0.0
        while True:
            if await request.is_disconnected():
                break

            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=POLL_SECONDS
            )
            if message is None:
                idle += POLL_SECONDS
                if idle >= HEARTBEAT_SECONDS:
                    idle = 0.0
                    yield ": keepalive\n\n"
                continue

            idle = 0.0
            try:
                envelope = json.loads(message["data"])
            except (TypeError, ValueError):
                continue
            yield f"event: {envelope.get('event', 'message')}\n"
            yield f"data: {json.dumps(envelope.get('data', {}))}\n\n"
    except asyncio.CancelledError:
        raise
    finally:
        try:
            await pubsub.unsubscribe(*channels)
            await pubsub.aclose()
        except Exception as exc:
            logger.warning("sse_cleanup_failed", error=str(exc))


@router.get("/stream")
async def stream_events(
    request: Request,
    ticket: str = Query(..., description="Single-use ticket from POST /events/ticket"),
):
    user_id, role = await _resolve_subscriber(ticket)
    channels = [event_service.user_channel(user_id), event_service.role_channel(role)]
    return StreamingResponse(
        _event_stream(request, channels),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
