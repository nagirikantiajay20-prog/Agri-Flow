import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import Pagination, require_permission
from app.schemas.common import PaginatedResponse, SuccessResponse
from app.schemas.common import Pagination as PaginationSchema
from app.schemas.seed import SeedPurchaseCreate, SeedPurchasePublic, SeedPurchaseStatusUpdate
from app.services import purchase_service

router = APIRouter(prefix="/seed-purchases", tags=["seed-purchases"])


@router.post("", response_model=SuccessResponse[SeedPurchasePublic], status_code=201)
async def create_purchase(
    body: SeedPurchaseCreate,
    farmer: Annotated[object, Depends(require_permission("seed.purchase"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    # This is the concurrency-critical path (Master Plan Module 6) — see
    # app.services.purchase_service.purchase_seeds for the row-locked
    # transactional implementation.
    purchase = await purchase_service.purchase_seeds(
        db,
        farmer=farmer,
        seed_id=body.seed_id,
        quantity_kg=body.quantity_kg,
        warehouse_id=body.warehouse_id,
        payment_method=body.payment_method,
        upi_id=body.upi_id,
    )
    return SuccessResponse(data=SeedPurchasePublic.model_validate(purchase))


@router.get("", response_model=PaginatedResponse[SeedPurchasePublic])
async def list_purchases(
    params: Pagination,
    actor: Annotated[object, Depends(require_permission("seed.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    purchases, total = await purchase_service.list_purchases_for_actor(db, actor=actor, params=params)
    total_pages = (total + params.page_size - 1) // params.page_size if total else 0
    return PaginatedResponse(
        data=[SeedPurchasePublic.model_validate(p) for p in purchases],
        pagination=PaginationSchema(page=params.page, page_size=params.page_size, total=total, total_pages=total_pages),
    )


@router.patch("/{purchase_id}/status", response_model=SuccessResponse[SeedPurchasePublic])
async def update_purchase_status(
    purchase_id: uuid.UUID,
    body: SeedPurchaseStatusUpdate,
    admin: Annotated[object, Depends(require_permission("seed_purchase.review"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    purchase = await purchase_service.update_purchase_status(
        db, admin=admin, purchase_id=purchase_id, new_status=body.payment_status
    )
    return SuccessResponse(data=SeedPurchasePublic.model_validate(purchase))
