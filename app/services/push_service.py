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
    raw = settings.FIREBASE_CREDENTIALS_JSON
    if raw and raw.strip():
        raw_str = raw.strip()
        # Handle wrapped single or double quotes from environment
        if (raw_str.startswith("'") and raw_str.endswith("'")) or (raw_str.startswith('"') and raw_str.endswith('"')):
            raw_str = raw_str[1:-1].strip()
        try:
            parsed = json.loads(raw_str)
            if isinstance(parsed, dict) and "private_key" in parsed:
                # Normalize newlines in private key
                parsed["private_key"] = parsed["private_key"].replace("\\n", "\n")
            return parsed
        except Exception as e:
            logger.error("fcm_credential_json_parse_failed", error=str(e))

    return settings.FIREBASE_CREDENTIALS_PATH or settings.GOOGLE_APPLICATION_CREDENTIALS or None


@lru_cache
def _firebase_app():
    source = _credential_source()
    if source is None:
        logger.warning("fcm_no_credential_source_available")
        return None
    import firebase_admin
    from firebase_admin import credentials

    try:
        return firebase_admin.get_app("agriflow")
    except ValueError:
        pass

    try:
        cred = credentials.Certificate(source)
        return firebase_admin.initialize_app(cred, name="agriflow")
    except Exception as exc:
        logger.error("fcm_app_init_failed", error=str(exc))
        return None


def project_id() -> str | None:
    app = _firebase_app() if is_enabled() else None
    return app.project_id if app else None


def is_enabled() -> bool:
    try:
        app = _firebase_app()
        return app is not None
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
    logger.info(
        "fcm_send_response",
        success_count=response.success_count,
        failure_count=response.failure_count,
        token_count=len(tokens),
    )
    for idx, resp in enumerate(response.responses):
        if not resp.success:
            logger.warning(
                "fcm_single_token_error",
                index=idx,
                error=str(resp.exception),
                error_type=type(resp.exception).__name__,
            )
    return _dead_tokens(tokens, response)


def _multicast(messaging, tokens: list[str], push: Push):
    """`tokens` are FCM registration tokens — what Flutter's
    FirebaseMessaging.getToken() returns."""
    data_dict = {k: str(v) for k, v in push.data.items()}
    data_dict.setdefault("title", push.title)
    data_dict.setdefault("body", push.body)
    return messaging.MulticastMessage(
        tokens=tokens,
        notification=messaging.Notification(title=push.title, body=push.body),
        data=data_dict,
        android=messaging.AndroidConfig(
            priority="high",
            notification=messaging.AndroidNotification(
                channel_id="high_importance_channel",
                priority="high",
                default_sound=True,
                default_vibrate_timings=True,
                click_action="FLUTTER_NOTIFICATION_CLICK",
            ),
        ),
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
                logger.info(
                    "fcm_dispatch_attempt",
                    user_id=str(push.user_id),
                    token_count=len(tokens),
                    title=push.title,
                )
                if not tokens:
                    logger.warning("fcm_no_tokens_found_for_user", user_id=str(push.user_id))
                    continue
                dead = await asyncio.to_thread(_send_blocking, tokens, push)
                if dead:
                    logger.info("fcm_removing_dead_tokens", dead_count=len(dead))
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


async def send_test_push(db: AsyncSession, *, user_id: uuid.UUID) -> dict:
    """Diagnostic tool: sends an immediate FCM test push to the user's active device tokens."""
    from app.models.user import FcmDeviceToken

    rows = await db.execute(
        select(FcmDeviceToken.fcm_token, FcmDeviceToken.device_type).where(
            FcmDeviceToken.user_id == user_id
        )
    )
    results = rows.all()
    tokens = [r[0] for r in results]

    if not is_enabled():
        return {
            "status": "disabled",
            "message": "FCM is not enabled (Firebase credentials missing or invalid)",
            "project_id": project_id(),
            "token_count": len(tokens),
        }

    if not tokens:
        return {
            "status": "no_tokens",
            "message": "No registered device tokens found for this user in fcm_device_tokens",
            "project_id": project_id(),
            "token_count": 0,
        }

    push = Push(
        user_id=user_id,
        title="FCM Diagnostic Test",
        body="Real backend-to-device FCM delivery verified successfully.",
        data={
            "notification_id": str(uuid.uuid4()),
            "type": "diagnostic",
            "reference_type": "diagnostic",
            "route": "/notifications",
            "title": "FCM Diagnostic Test",
            "body": "Real backend-to-device FCM delivery verified successfully.",
        },
    )

    dead = await asyncio.to_thread(_send_blocking, tokens, push)
    if dead:
        await db.execute(delete(FcmDeviceToken).where(FcmDeviceToken.fcm_token.in_(dead)))

    return {
        "status": "sent",
        "project_id": project_id(),
        "token_count": len(tokens),
        "tokens_preview": [f"******{t[-6:]}" if len(t) > 6 else t for t in tokens],
        "dead_tokens_removed": len(dead),
    }
