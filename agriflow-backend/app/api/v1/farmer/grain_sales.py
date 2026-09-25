import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.api.v1.farmer._mappers import booking_out, offer_out, paginated
from app.core.database import get_db
from app.core.dependencies import Pagination, require_farmer
from app.models.enums import BookingStatus
from app.models.user import User
from app.models.warehouse import BookingSlot, Warehouse, WarehouseSlot
from app.schemas import farmer_app as s
from app.schemas.common import MessageResponse, PaginatedResponse, SuccessResponse
from app.services import booking_service, grain_sale_service

router = APIRouter(prefix="/grain-sales", tags=["farmer · grain sales"])


@router.post("/book-slot", response_model=SuccessResponse[s.BookSlotOut], status_code=201)
async def book_slot(
    body: s.BookSlotIn,
    farmer: Annotated[User, Depends(require_farmer("booking.create"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
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
    return SuccessResponse(
        data=s.BookSlotOut(booking_id=booking.id, status=booking.status),
        message="Slot booked successfully",
    )


@router.get("/bookings", response_model=PaginatedResponse[s.BookingOut])
async def list_bookings(
    params: Pagination,
    farmer: Annotated[User, Depends(require_farmer("booking.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    slot = aliased(WarehouseSlot)
    base = (
        select(BookingSlot, Warehouse, slot)
        .join(Warehouse, Warehouse.id == BookingSlot.warehouse_id)
        .outerjoin(slot, slot.id == BookingSlot.warehouse_slot_id)
        .where(BookingSlot.farmer_id == farmer.id)
    )
    if params.status:
        base = base.where(BookingSlot.status == params.status)
    total = (
        await db.execute(select(func.count()).select_from(base.with_only_columns(BookingSlot.id).subquery()))
    ).scalar_one()
    rows = (
        await db.execute(
            base.order_by(BookingSlot.created_at.desc())
            .offset((params.page - 1) * params.page_size)
            .limit(params.page_size)
        )
    ).all()
    return paginated([booking_out(b, w, sl) for b, w, sl in rows], total, params)


@router.delete("/bookings/{booking_id}", response_model=MessageResponse)
async def cancel_booking(
    booking_id: uuid.UUID,
    farmer: Annotated[User, Depends(require_farmer("booking.cancel.own"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await booking_service.update_booking_status(
        db, actor=farmer, booking_id=booking_id, new_status=BookingStatus.CANCELLED, notes=None
    )
    return MessageResponse(message="Booking slot cancelled")


@router.post("/offers", response_model=SuccessResponse[s.GrainOfferOut], status_code=201)
async def submit_offer(
    body: s.GrainOfferIn,
    farmer: Annotated[User, Depends(require_farmer("grain_sale.create"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    sale = await grain_sale_service.create_grain_sale(
        db,
        farmer=farmer,
        grain_type=body.crop_type,
        grade=body.grade,
        raw_material_kg=body.quantity_kg,
        crop_id=body.crop_id,
        offered_price_per_kg=body.price_per_kg,
        notes=body.notes,
    )
    return SuccessResponse(data=offer_out(sale), message="Grain sale offer submitted")


@router.get("/offers", response_model=PaginatedResponse[s.GrainOfferOut])
async def list_offers(
    params: Pagination,
    farmer: Annotated[User, Depends(require_farmer("grain_sale.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    sales, total = await grain_sale_service.list_grain_sales_for_actor(db, actor=farmer, params=params)
    return paginated([offer_out(x) for x in sales], total, params)
