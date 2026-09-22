import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.farmer._mappers import paginated
from app.core.database import get_db
from app.core.dependencies import Pagination, require_farmer
from app.models.user import User
from app.schemas import farmer_app as s
from app.schemas.common import MessageResponse, PaginatedResponse, SuccessResponse
from app.core.logging import get_logger
from app.services import notification_read_service, push_service

logger = get_logger(__name__)

router = APIRouter(prefix="/notifications", tags=["farmer · notifications"])


@router.get("", response_model=PaginatedResponse[s.NotificationOut])
async def list_notifications(
    params: Pagination,
    farmer: Annotated[User, Depends(require_farmer("notification.read.own"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    notes, total = await notification_read_service.list_for_user(db, user_id=farmer.id, params=params)
    return paginated([s.NotificationOut.model_validate(n, from_attributes=True) for n in notes], total, params)


@router.get("/unread-count", response_model=SuccessResponse[s.UnreadCountOut])
async def unread_count(
    farmer: Annotated[User, Depends(require_farmer("notification.read.own"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    count = await notification_read_service.unread_count(db, user_id=farmer.id)
    return SuccessResponse(data=s.UnreadCountOut(unread_count=count))


@router.patch("/{notification_id}/read", response_model=MessageResponse)
@router.put("/{notification_id}/read", response_model=MessageResponse, include_in_schema=False)
async def mark_read(
    notification_id: uuid.UUID,
    farmer: Annotated[User, Depends(require_farmer("notification.read.own"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await notification_read_service.mark_read(db, user_id=farmer.id, notification_id=notification_id)
    return MessageResponse(message="Marked as read")


@router.post("/read-all", response_model=MessageResponse)
@router.put("/read-all", response_model=MessageResponse, include_in_schema=False)
async def mark_all_read(
    farmer: Annotated[User, Depends(require_farmer("notification.read.own"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    count = await notification_read_service.mark_all_read(db, user_id=farmer.id)
    return MessageResponse(message=f"Marked {count} notification(s) as read")


@router.post("/fcm-token", response_model=MessageResponse)
async def register_fcm_token(
    body: s.FcmTokenIn,
    farmer: Annotated[User, Depends(require_farmer("device.register.own"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    token_preview = f"******{body.fcm_token[-6:]}" if len(body.fcm_token) > 6 else body.fcm_token
    logger.info(
        "fcm_token_registration_received",
        user_id=str(farmer.id),
        user_phone=farmer.phone,
        token_fingerprint=token_preview,
        device_type=body.device_type,
    )
    await push_service.register_token(
        db, user_id=farmer.id, fcm_token=body.fcm_token, device_type=body.device_type
    )
    return MessageResponse(message="Device registered for push notifications")


@router.post("/test-fcm", response_model=SuccessResponse[dict])
async def trigger_test_fcm(
    farmer: Annotated[User, Depends(require_farmer("device.register.own"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    logger.info("fcm_test_push_requested", user_id=str(farmer.id), user_phone=farmer.phone)
    result = await push_service.send_test_push(db, user_id=farmer.id)
    return SuccessResponse(data=result, message="Test push dispatched")
