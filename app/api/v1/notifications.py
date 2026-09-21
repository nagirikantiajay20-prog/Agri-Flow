import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import Pagination, require_permission
from app.schemas.admin import NotificationPublic
from app.schemas.common import MessageResponse, PaginatedResponse
from app.schemas.common import Pagination as PaginationSchema
from app.services import notification_read_service

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=PaginatedResponse[NotificationPublic])
async def list_notifications(
    params: Pagination,
    user: Annotated[object, Depends(require_permission("notification.read.own"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    notes, total = await notification_read_service.list_for_user(db, user_id=user.id, params=params)
    total_pages = (total + params.page_size - 1) // params.page_size if total else 0
    return PaginatedResponse(
        data=[NotificationPublic.model_validate(n) for n in notes],
        pagination=PaginationSchema(page=params.page, page_size=params.page_size, total=total, total_pages=total_pages),
    )


@router.patch("/{notification_id}/read", response_model=MessageResponse)
async def mark_read(
    notification_id: uuid.UUID,
    user: Annotated[object, Depends(require_permission("notification.read.own"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await notification_read_service.mark_read(db, user_id=user.id, notification_id=notification_id)
    return MessageResponse(message="Marked as read")


@router.patch("/read-all", response_model=MessageResponse)
async def mark_all_read(
    user: Annotated[object, Depends(require_permission("notification.read.own"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    count = await notification_read_service.mark_all_read(db, user_id=user.id)
    return MessageResponse(message=f"Marked {count} notification(s) as read")
