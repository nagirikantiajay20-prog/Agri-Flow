import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import Pagination, require_permission
from app.schemas.common import PaginatedResponse, SuccessResponse
from app.schemas.common import Pagination as PaginationSchema
from app.schemas.grain import (
    GrainProcureRequest,
    GrainSaleCreate,
    GrainSalePayRequest,
    GrainSalePublic,
    GrainSaleReviewRequest,
    GrainYieldUpdateRequest,
)
from app.services import grain_sale_service

router = APIRouter(prefix="/grain-sales", tags=["grain-sales"])


@router.post("", response_model=SuccessResponse[GrainSalePublic], status_code=201)
async def create_grain_sale(
    body: GrainSaleCreate,
    farmer: Annotated[object, Depends(require_permission("grain_sale.create"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    sale = await grain_sale_service.create_grain_sale(
        db, farmer=farmer, grain_type=body.grain_type, grade=body.grade,
        raw_material_kg=body.raw_material_kg, crop_id=body.crop_id,
    )
    return SuccessResponse(data=GrainSalePublic.model_validate(sale))


@router.get("", response_model=PaginatedResponse[GrainSalePublic])
async def list_grain_sales(
    params: Pagination,
    actor: Annotated[object, Depends(require_permission("grain_sale.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    sales, total = await grain_sale_service.list_grain_sales_for_actor(db, actor=actor, params=params)
    total_pages = (total + params.page_size - 1) // params.page_size if total else 0
    return PaginatedResponse(
        data=[GrainSalePublic.model_validate(s) for s in sales],
        pagination=PaginationSchema(page=params.page, page_size=params.page_size, total=total, total_pages=total_pages),
    )


@router.patch("/{sale_id}/review", response_model=SuccessResponse[GrainSalePublic])
async def review_grain_sale(
    sale_id: uuid.UUID,
    body: GrainSaleReviewRequest,
    manager: Annotated[object, Depends(require_permission("grain_sale.review"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    sale = await grain_sale_service.review_grain_sale(
        db,
        manager=manager,
        sale_id=sale_id,
        booking_slot_id=body.booking_slot_id,
        good_quantity_kg=body.good_quantity_kg,
        bad_quantity_kg=body.bad_quantity_kg,
        rejection_reason=body.rejection_reason,
        approve=body.approve,
    )
    return SuccessResponse(data=GrainSalePublic.model_validate(sale))


@router.patch("/{sale_id}/pay", response_model=SuccessResponse[GrainSalePublic])
async def pay_grain_sale(
    sale_id: uuid.UUID,
    body: GrainSalePayRequest,
    manager: Annotated[object, Depends(require_permission("grain_sale.pay"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    sale = await grain_sale_service.pay_grain_sale(
        db, manager=manager, sale_id=sale_id, payment_reference=body.payment_reference
    )
    return SuccessResponse(data=GrainSalePublic.model_validate(sale))


@router.post("/procure", response_model=SuccessResponse[GrainSalePublic], status_code=201)
async def procure_grain(
    body: GrainProcureRequest,
    manager: Annotated[object, Depends(require_permission("procurement.manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    sale = await grain_sale_service.procure_grain(
        db, manager=manager, farmer_id=body.farmer_id, grain_type=body.grain_type,
        grade=body.grade, raw_material_kg=body.raw_material_kg,
        good_material_kg=body.good_material_kg, wastage_kg=body.wastage_kg,
    )
    return SuccessResponse(data=GrainSalePublic.model_validate(sale))


@router.patch("/{sale_id}/yield", response_model=SuccessResponse[GrainSalePublic])
async def update_grain_yield(
    sale_id: uuid.UUID,
    body: GrainYieldUpdateRequest,
    manager: Annotated[object, Depends(require_permission("procurement.manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    sale = await grain_sale_service.update_yield(
        db, manager=manager, sale_id=sale_id,
        good_material_kg=body.good_material_kg, wastage_kg=body.wastage_kg,
    )
    return SuccessResponse(data=GrainSalePublic.model_validate(sale))
