import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import require_permission
from app.core.exceptions import NotFoundError
from app.models.enums import UserRole
from app.models.seed import Seed
from app.models.warehouse import Warehouse
from app.schemas.common import SuccessResponse
from app.schemas.seed import SeedCreate, SeedPublic, SeedUpdate, SeedWarehouseAssign
from app.schemas.warehouse import WarehouseBrief
from app.services import purchase_service

router = APIRouter(prefix="/seeds", tags=["seeds"])


def _to_seed_public(seed: Seed, warehouses: list[Warehouse] | None = None) -> SeedPublic:
    wh_list = warehouses or []
    briefs = [
        WarehouseBrief(
            id=w.id,
            name=w.name,
            address=w.address,
            location=w.location,
            contact_number=w.contact_number,
        )
        for w in wh_list
    ]
    return SeedPublic(
        id=seed.id,
        name=seed.name,
        crop_type=seed.crop_type,
        variety=seed.variety,
        price_per_kg=seed.price_per_kg,
        price_grade_a=seed.price_grade_a,
        price_grade_b=seed.price_grade_b,
        price_grade_c=seed.price_grade_c,
        max_order_quantity_kg=seed.max_order_quantity_kg,
        stock_kg=seed.stock_kg,
        warehouse_id=seed.warehouse_id,
        warehouse_ids=[w.id for w in wh_list],
        warehouses=briefs,
        description=seed.description,
        image_url=seed.image_url,
        is_active=seed.is_active,
    )


@router.get("", response_model=SuccessResponse[list[SeedPublic]])
async def list_seeds(
    actor: Annotated[object, Depends(require_permission("seed.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    seeds = await purchase_service.list_seeds(db, active_only=(actor.role == UserRole.FARMER))
    wh_map = await purchase_service.get_warehouses_for_seeds(db, [s.id for s in seeds])
    return SuccessResponse(data=[_to_seed_public(s, wh_map.get(s.id, [])) for s in seeds])


@router.get("/{seed_id}", response_model=SuccessResponse[SeedPublic])
async def get_seed(
    seed_id: uuid.UUID,
    _: Annotated[object, Depends(require_permission("seed.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    seed = await db.get(Seed, seed_id)
    if seed is None:
        raise NotFoundError("Seed not found")
    warehouses = await purchase_service.list_seed_warehouses(db, seed.id)
    return SuccessResponse(data=_to_seed_public(seed, warehouses))


@router.post("", response_model=SuccessResponse[SeedPublic], status_code=201)
async def create_seed(
    body: SeedCreate,
    admin: Annotated[object, Depends(require_permission("seed.manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    seed = await purchase_service.create_seed(db, admin=admin, **body.model_dump())
    warehouses = await purchase_service.list_seed_warehouses(db, seed.id)
    return SuccessResponse(data=_to_seed_public(seed, warehouses))


@router.patch("/{seed_id}", response_model=SuccessResponse[SeedPublic])
async def update_seed(
    seed_id: uuid.UUID,
    body: SeedUpdate,
    admin: Annotated[object, Depends(require_permission("seed.manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    seed = await purchase_service.update_seed(db, admin=admin, seed_id=seed_id, **body.model_dump(exclude_unset=True))
    warehouses = await purchase_service.list_seed_warehouses(db, seed.id)
    return SuccessResponse(data=_to_seed_public(seed, warehouses))


@router.delete("/{seed_id}", status_code=204)
async def delete_seed(
    seed_id: uuid.UUID,
    admin: Annotated[object, Depends(require_permission("seed.manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await purchase_service.delete_seed(db, admin=admin, seed_id=seed_id)


# ── Seed-Warehouse Sub-resource Endpoints (Option B) ──────────────────────────


@router.get("/{seed_id}/warehouses", response_model=SuccessResponse[list[WarehouseBrief]])
async def list_seed_warehouses(
    seed_id: uuid.UUID,
    _: Annotated[object, Depends(require_permission("seed.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    warehouses = await purchase_service.list_seed_warehouses(db, seed_id)
    return SuccessResponse(
        data=[
            WarehouseBrief(
                id=w.id,
                name=w.name,
                address=w.address,
                location=w.location,
                contact_number=w.contact_number,
            )
            for w in warehouses
        ]
    )


@router.post("/{seed_id}/warehouses", response_model=SuccessResponse[list[WarehouseBrief]])
async def assign_seed_warehouses(
    seed_id: uuid.UUID,
    body: SeedWarehouseAssign,
    admin: Annotated[object, Depends(require_permission("seed.manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    warehouses = await purchase_service.assign_seed_warehouses(
        db, admin=admin, seed_id=seed_id, warehouse_ids=body.warehouse_ids
    )
    return SuccessResponse(
        data=[
            WarehouseBrief(
                id=w.id,
                name=w.name,
                address=w.address,
                location=w.location,
                contact_number=w.contact_number,
            )
            for w in warehouses
        ]
    )


@router.delete("/{seed_id}/warehouses/{warehouse_id}", status_code=204)
async def remove_seed_warehouse(
    seed_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    admin: Annotated[object, Depends(require_permission("seed.manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await purchase_service.remove_seed_warehouse(
        db, admin=admin, seed_id=seed_id, warehouse_id=warehouse_id
    )
