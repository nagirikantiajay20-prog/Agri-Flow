import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.ledger import Notification
from app.schemas.common import PageParams


async def list_for_user(db: AsyncSession, *, user_id: uuid.UUID, params: PageParams) -> tuple[list[Notification], int]:
    from sqlalchemy import func

    query = select(Notification).where(Notification.user_id == user_id)
    count_query = select(func.count()).select_from(Notification).where(Notification.user_id == user_id)
    total = (await db.execute(count_query)).scalar_one()
    query = (
        query.order_by(Notification.created_at.desc())
        .offset((params.page - 1) * params.page_size)
        .limit(params.page_size)
    )
    rows = (await db.execute(query)).scalars().all()
    return list(rows), total


async def mark_read(db: AsyncSession, *, user_id: uuid.UUID, notification_id: uuid.UUID) -> Notification:
    note = await db.get(Notification, notification_id)
    if note is None or note.user_id != user_id:
        # Deliberately identical error for "not found" and "not yours" —
        # do not leak existence of another user's notification.
        raise NotFoundError("Notification not found")
    note.is_read = True
    await db.flush()
    return note


async def mark_all_read(db: AsyncSession, *, user_id: uuid.UUID) -> int:
    result = await db.execute(
        update(Notification).where(Notification.user_id == user_id, Notification.is_read.is_(False)).values(is_read=True)
    )
    await db.flush()
    return result.rowcount or 0


async def unread_count(db: AsyncSession, *, user_id: uuid.UUID) -> int:
    from sqlalchemy import func

    result = await db.execute(
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user_id, Notification.is_read.is_(False))
    )
    return int(result.scalar_one())
