"""
Firebase Cloud Messaging for the farmer Android app.

Pushes are queued on the SQLAlchemy session by notification_service and
only dispatched after the request's transaction commits
(app.core.database.get_db). A push for an order that then rolled back —
say, one that failed its stock check — would tell the farmer something
that never happened.

Credentials come from, in order: FIREBASE_CREDENTIALS_JSON (the
service-account JSON itself, convenient for Render environment
variables), FIREBASE_CREDENTIALS_PATH, or GOOGLE_APPLICATION_CREDENTIALS
(a mounted secret file). With none set, push is disabled and in-app
notifications keep working unchanged.
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


def _credential_source() -> dict | str | None:
    if settings.FIREBASE_CREDENTIALS_JSON:
        return json.loads(settings.FIREBASE_CREDENTIALS_JSON)
    return settings.FIREBASE_CREDENTIALS_PATH or settings.GOOGLE_APPLICATION_CREDENTIALS or None


@lru_cache
def _firebase_app():
    source = _credential_source()
    if source is None:
        return None
    import firebase_admin
    from firebase_admin import credentials

    return firebase_admin.initialize_app(credentials.Certificate(source), name="agriflow")


def project_id() -> str | None:
    app = _firebase_app() if is_enabled() else None
    return app.project_id if app else None


def is_enabled() -> bool:
    try:
        return _firebase_app() is not None
    except Exception as exc:
        logger.error("fcm_init_failed", error=str(exc))
        return False


def _send_blocking(tokens: list[str], push: Push) -> list[str]:
    """Returns the tokens FCM reported as permanently invalid."""
    import warnings

    from firebase_admin import messaging

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="MulticastMessage.tokens is deprecated")
        message = _multicast(messaging, tokens, push)
    response = messaging.send_each_for_multicast(message, app=_firebase_app())
    return _dead_tokens(tokens, response)


def _multicast(messaging, tokens: list[str], push: Push):
    """`tokens` are FCM registration tokens — what Flutter's
    FirebaseMessaging.getToken() returns. firebase-admin 7 deprecates the
    parameter in favour of `fids`, but those are Firebase Installation IDs,
    a different identifier; passing registration tokens there would break
    delivery. requirements.txt pins firebase-admin below 8 until the client
    side moves to FIDs."""
    return messaging.MulticastMessage(
        tokens=tokens,
        notification=messaging.Notification(title=push.title, body=push.body),
        data=push.data,
        android=messaging.AndroidConfig(priority="high"),
    )


def _dead_tokens(tokens: list[str], response) -> list[str]:
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
