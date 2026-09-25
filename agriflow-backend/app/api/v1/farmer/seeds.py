import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.farmer._mappers import paginated, seed_out
from app.core.database import get_db
from app.core.dependencies import Pagination, require_farmer
from app.core.exceptions import NotFoundError
from app.models.seed import Seed, SeedPurchase
from app.models.user import User
from app.models.warehouse import Warehouse
from app.schemas import farmer_app as s
from app.schemas.common import PaginatedResponse, SuccessResponse
from app.services import farmer_app_service, purchase_service

router = APIRouter(prefix="/seeds", tags=["farmer · seeds"])


@router.get("", response_model=SuccessResponse[list[s.SeedOut]])
async def list_seeds(
    _: Annotated[User, Depends(require_farmer("seed.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    q: str | None = Query(None, max_length=100, description="Search name or variety"),
    crop_type: str | None = Query(None, description="Cotton, Rice, ... or All"),
    min_price: Decimal | None = Query(None, ge=0),
    max_price: Decimal | None = Query(None, ge=0),
    warehouse_id: uuid.UUID | None = Query(None),
    in_stock_only: bool = Query(False),
):
    seeds = await purchase_service.list_seeds_filtered(
        db, q=q, crop_type=crop_type, min_price=min_price, max_price=max_price,
        warehouse_id=warehouse_id, in_stock_only=in_stock_only,
    )
    return SuccessResponse(data=[seed_out(x) for x in seeds])


@router.post("/purchase", response_model=SuccessResponse[s.SeedPurchaseReceipt], status_code=201)
async def purchase(
    body: s.SeedPurchaseIn,
    farmer: Annotated[User, Depends(require_farmer("seed.purchase"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    warehouse_id = body.warehouse_id
    if warehouse_id is None:
        warehouse_id = (await db.execute(select(Seed.warehouse_id).where(Seed.id == body.seed_id))).scalar()
    warehouse = await db.get(Warehouse, warehouse_id) if warehouse_id else None
    if body.warehouse_id is not None and (warehouse is None or not warehouse.is_active):
        raise NotFoundError("Warehouse not found")

    order = await purchase_service.purchase_seeds(
        db,
        farmer=farmer,
        seed_id=body.seed_id,
        quantity_kg=body.quantity_kg,
        warehouse_id=warehouse_id,
        payment_method=body.payment_method,
        upi_id=None,
        grade=body.grade.value if hasattr(body.grade, "value") else (str(body.grade) if body.grade else None),
        pickup_date=body.pickup_date,
    )
    seed = await db.get(Seed, body.seed_id)
    return SuccessResponse(
        data=s.SeedPurchaseReceipt(
            order_id=order.id,
            invoice_number=order.invoice_number,
            seed_name=seed.name,
            quantity_kg=Decimal(order.quantity_kg),
            grade=order.grade,
            warehouse_id=warehouse_id,
            warehouse_name=warehouse.name if warehouse else None,
            pickup_date=order.pickup_date,
            price_per_kg=Decimal(order.price_per_kg),
            total_amount=Decimal(order.total_amount),
            payment_status=order.payment_status,
            payment_status_label=farmer_app_service.payment_status_label(order.payment_status, order.payment_method),
        ),
        message="Order placed successfully!",
    )


@router.get("/purchases", response_model=PaginatedResponse[s.SeedPurchaseOut])
async def purchase_history(
    params: Pagination,
    farmer: Annotated[User, Depends(require_farmer("seed.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    base = select(SeedPurchase, Seed).join(Seed, Seed.id == SeedPurchase.seed_id).where(SeedPurchase.farmer_id == farmer.id)
    if params.status:
        base = base.where(SeedPurchase.payment_status == params.status)
    total = (
        await db.execute(select(func.count()).select_from(base.with_only_columns(SeedPurchase.id).subquery()))
    ).scalar_one()
    rows = (
        await db.execute(
            base.order_by(SeedPurchase.created_at.desc())
            .offset((params.page - 1) * params.page_size)
            .limit(params.page_size)
        )
    ).all()
    data = [
        s.SeedPurchaseOut(
            id=p.id,
            invoice_number=p.invoice_number,
            quantity_kg=Decimal(p.quantity_kg),
            price_per_kg=Decimal(p.price_per_kg),
            total_amount=Decimal(p.total_amount),
            grade=p.grade,
            payment_status=p.payment_status,
            pickup_date=p.pickup_date,
            warehouse_id=p.warehouse_id,
            created_at=p.created_at,
            seed=seed_out(seed),
        )
        for p, seed in rows
    ]
    return paginated(data, total, params)


@router.get("/{seed_id}", response_model=SuccessResponse[s.SeedOut])
async def get_seed(
    seed_id: uuid.UUID,
    _: Annotated[User, Depends(require_farmer("seed.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    # Registered last so it never shadows the literal /purchase(s) paths
    # above — FastAPI matches routes in declaration order, and a
    # {seed_id}: UUID route declared first would intercept "/purchases"
    # and fail its UUID conversion instead of falling through.
    seed = await db.get(Seed, seed_id)
    if seed is None:
        raise NotFoundError("Seed not found")
    return SuccessResponse(data=seed_out(seed))
