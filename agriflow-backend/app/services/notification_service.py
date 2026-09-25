"""
Single insertion point for notifications (Master Plan Module 10).

Every module that used to insert into `notifications` directly from
multiple call sites (a real duplicate-notification bug class already
seen once in production — see Master Plan §1.4,
20260714070000_fix_duplicate_notifications.sql) now calls one of the
functions below instead.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import NotificationType, UserRole
from app.models.ledger import Notification
from app.models.user import User
from app.services import event_service, push_service


async def notify_user(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    title: str,
    message: str,
    type_: NotificationType = NotificationType.INFO,
    reference_type: str | None = None,
    reference_id: uuid.UUID | None = None,
) -> Notification:
    note = Notification(
        user_id=user_id,
        title=title,
        message=message,
        type=type_,
        reference_type=reference_type,
        reference_id=reference_id,
    )
    db.add(note)
    await db.flush()
    push_service.enqueue(
        db,
        push_service.Push(
            user_id=user_id,
            title=title,
            body=message,
            data={
                "notification_id": str(note.id),
                "type": type_.value,
                "reference_type": reference_type or "",
                "reference_id": str(reference_id) if reference_id else "",
            },
        ),
    )
    await event_service.publish_to_user(
        user_id,
        event_service.EVENT_NOTIFICATION,
        {"id": str(note.id), "title": title, "message": message, "type": type_.value},
    )
    return note


async def notify_roles(
    db: AsyncSession,
    *,
    roles: list[UserRole],
    title: str,
    message: str,
    type_: NotificationType = NotificationType.INFO,
    reference_type: str | None = None,
    reference_id: uuid.UUID | None = None,
) -> list[Notification]:
    """Fan-out to every active user with one of the given roles — e.g.
    notify every manager + super_admin when a farmer requests a bank
    change (ports the verified fan-out in
    agriflow-web/supabase/functions/farmer-api requestBankChange)."""
    result = await db.execute(select(User.id).where(User.role.in_(roles)))
    user_ids = [row[0] for row in result.all()]
    notes = [
        Notification(
            user_id=uid,
            title=title,
            message=message,
            type=type_,
            reference_type=reference_type,
            reference_id=reference_id,
        )
        for uid in user_ids
    ]
    db.add_all(notes)
    await db.flush()
    for note in notes:
        await event_service.publish_to_user(
            note.user_id,
            event_service.EVENT_NOTIFICATION,
            {"id": str(note.id), "title": title, "message": message, "type": type_.value},
        )
    return notes
