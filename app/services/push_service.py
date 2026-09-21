"""
Firebase Cloud Messaging for the farmer Android app.

Pushes are queued on the SQLAlchemy session by notification_service and
only dispatched after the request's transaction commits
(app.core.database.get_db). A push for an order that then rolled back —
say, one that failed its stock check — would tell the farmer something
that never happened.

Credentials come from FIREBASE_CREDENTIALS_JSON (the service-account JSON
itself, convenient for Render environment variables) or
FIREBASE_CREDENTIALS_PATH (a mounted secret file). With neither set, push
is disabled and in-app notifications keep working unchanged.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

SESSION_QUEUE_KEY = "pending_pushes"

_background: set[asyncio.Task] = set()


@dataclass(frozen=True)
class Push:
    user_id: uuid.UUID
    title: str
    body: str
    data: dict[str, str]


def enqueue(db: AsyncSession, push: Push) -> None:
    db.info.setdefault(SESSION_QUEUE_KEY, []).append(push)


def drain(db: AsyncSession) -> list[Push]:
    return db.info.pop(SESSION_QUEUE_KEY, [])


def discard(db: AsyncSession) -> None:
    db.info.pop(SESSION_QUEUE_KEY, None)


@lru_cache
def _firebase_app():
    if not (settings.FIREBASE_CREDENTIALS_JSON or settings.FIREBASE_CREDENTIALS_PATH):
        return None
    import firebase_admin
    from firebase_admin import credentials

    source = (
        json.loads(settings.FIREBASE_CREDENTIALS_JSON)
        if settings.FIREBASE_CREDENTIALS_JSON
        else settings.FIREBASE_CREDENTIALS_PATH
    )
    return firebase_admin.initialize_app(credentials.Certificate(source), name="agriflow")


def is_enabled() -> bool:
    try:
        return _firebase_app() is not None
    except Exception as exc:
        logger.error("fcm_init_failed", error=str(exc))
        return False


def _send_blocking(tokens: list[str], push: Push) -> list[str]:
    """Returns the tokens FCM reported as permanently invalid."""
    from firebase_admin import messaging

    message = messaging.MulticastMessage(
        tokens=tokens,
        notification=messaging.Notification(title=push.title, body=push.body),
        data=push.data,
        android=messaging.AndroidConfig(priority="high"),
    )
    response = messaging.send_each_for_multicast(message, app=_firebase_app())
    dead = []
    for token, result in zip(tokens, response.responses, strict=False):
        if not result.success and type(result.exception).__name__ in {
            "UnregisteredError",
            "SenderIdMismatchError",
        }:
            dead.append(token)
    return dead


async def _deliver(pushes: list[Push]) -> None:
    from app.core.database import session_scope
    from app.models.user import FcmDeviceToken

    try:
        async with session_scope() as db:
            for push in pushes:
                rows = await db.execute(
                    select(FcmDeviceToken.fcm_token).where(FcmDeviceToken.user_id == push.user_id)
                )
                tokens = [r[0] for r in rows.all()]
                if not tokens:
                    continue
                dead = await asyncio.to_thread(_send_blocking, tokens, push)
                if dead:
                    await db.execute(delete(FcmDeviceToken).where(FcmDeviceToken.fcm_token.in_(dead)))
    except Exception as exc:
        logger.error("fcm_delivery_failed", error=str(exc), count=len(pushes))


def dispatch_after_commit(pushes: list[Push]) -> None:
    """Fire-and-forget: the HTTP response must not wait on Google."""
    if not pushes or not is_enabled():
        return
    task = asyncio.get_running_loop().create_task(_deliver(pushes))
    _background.add(task)
    task.add_done_callback(_background.discard)


async def register_token(db: AsyncSession, *, user_id: uuid.UUID, fcm_token: str, device_type: str) -> None:
    """A token identifies a device, not a user: if the phone changes hands
    (another farmer logs in on it), the token moves to the new user."""
    from app.models.user import FcmDeviceToken

    existing = (
        await db.execute(select(FcmDeviceToken).where(FcmDeviceToken.fcm_token == fcm_token))
    ).scalar_one_or_none()
    if existing is None:
        db.add(FcmDeviceToken(user_id=user_id, fcm_token=fcm_token, device_type=device_type))
    else:
        existing.user_id = user_id
        existing.device_type = device_type
    await db.flush()


async def unregister_token(db: AsyncSession, *, user_id: uuid.UUID, fcm_token: str) -> None:
    from app.models.user import FcmDeviceToken

    await db.execute(
        delete(FcmDeviceToken).where(
            FcmDeviceToken.fcm_token == fcm_token, FcmDeviceToken.user_id == user_id
        )
    )
    await db.flush()
