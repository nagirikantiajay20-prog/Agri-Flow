import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import Pagination, require_any_permission, require_permission
from app.schemas.common import PaginatedResponse, SuccessResponse
from app.schemas.common import Pagination as PaginationSchema
from app.schemas.grain import BookingInspectionRequest, CropInspectionPublic
from app.schemas.warehouse import BookingCreate, BookingPublic, BookingStatusUpdate
from app.services import booking_service, grain_sale_service

router = APIRouter(prefix="/bookings", tags=["bookings"])


@router.post("", response_model=SuccessResponse[BookingPublic], status_code=201)
async def create_booking(
    body: BookingCreate,
    farmer: Annotated[object, Depends(require_permission("booking.create"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    # Highest-concurrency-risk endpoint in the system (Master Plan Module
    # 7) — see app.services.booking_service.create_booking for the
    # row-locked transactional implementation that preserves the legacy
    # `create_booking_slot` RPC's guarantees.
    booking = await booking_service.create_booking(
        db,
        farmer=farmer,
        warehouse_id=body.warehouse_id,
        warehouse_slot_id=body.warehouse_slot_id,
        booking_date=body.booking_date,
        delivery_address=body.delivery_address,
        grain_type=body.grain_type,
        quantity_kg=body.quantity_kg,
        grain_sale_id=body.grain_sale_id,
        notes=body.notes,
    )
    return SuccessResponse(data=BookingPublic.model_validate(booking))


@router.get("", response_model=PaginatedResponse[BookingPublic])
async def list_bookings(
    params: Pagination,
    actor: Annotated[object, Depends(require_permission("booking.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    bookings, total = await booking_service.list_bookings_for_actor(db, actor=actor, params=params)
    total_pages = (total + params.page_size - 1) // params.page_size if total else 0
    return PaginatedResponse(
        data=[BookingPublic.model_validate(b) for b in bookings],
        pagination=PaginationSchema(page=params.page, page_size=params.page_size, total=total, total_pages=total_pages),
    )


@router.patch("/{booking_id}/status", response_model=SuccessResponse[BookingPublic])
async def update_booking_status(
    booking_id: uuid.UUID,
    body: BookingStatusUpdate,
    actor: Annotated[object, Depends(require_any_permission("booking.review", "booking.cancel.own"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    booking = await booking_service.update_booking_status(
        db, actor=actor, booking_id=booking_id, new_status=body.status, notes=body.notes
    )
    return SuccessResponse(data=BookingPublic.model_validate(booking))


@router.post("/{booking_id}/inspect", response_model=SuccessResponse[CropInspectionPublic], status_code=201)
async def inspect_booking(
    booking_id: uuid.UUID,
    body: BookingInspectionRequest,
    inspector: Annotated[object, Depends(require_permission("grain_sale.inspect"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    inspection = await grain_sale_service.inspect_booking(
        db, inspector=inspector, booking_id=booking_id,
        good_quantity_kg=body.good_quantity_kg, bad_quantity_kg=body.bad_quantity_kg,
        rejection_reason=body.rejection_reason, notes=body.notes,
    )
    return SuccessResponse(data=CropInspectionPublic.model_validate(inspection))
