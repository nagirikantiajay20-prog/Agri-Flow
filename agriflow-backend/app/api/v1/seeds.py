import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import require_permission
from app.models.enums import UserRole
from app.schemas.common import SuccessResponse
from app.schemas.seed import SeedCreate, SeedPublic, SeedUpdate
from app.services import purchase_service

router = APIRouter(prefix="/seeds", tags=["seeds"])


@router.get("", response_model=SuccessResponse[list[SeedPublic]])
async def list_seeds(
    actor: Annotated[object, Depends(require_permission("seed.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    seeds = await purchase_service.list_seeds(db, active_only=(actor.role == UserRole.FARMER))
    return SuccessResponse(data=[SeedPublic.model_validate(s) for s in seeds])


@router.post("", response_model=SuccessResponse[SeedPublic], status_code=201)
async def create_seed(
    body: SeedCreate,
    admin: Annotated[object, Depends(require_permission("seed.manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    seed = await purchase_service.create_seed(db, admin=admin, **body.model_dump())
    return SuccessResponse(data=SeedPublic.model_validate(seed))


@router.patch("/{seed_id}", response_model=SuccessResponse[SeedPublic])
async def update_seed(
    seed_id: uuid.UUID,
    body: SeedUpdate,
    admin: Annotated[object, Depends(require_permission("seed.manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    seed = await purchase_service.update_seed(db, admin=admin, seed_id=seed_id, **body.model_dump(exclude_unset=True))
    return SuccessResponse(data=SeedPublic.model_validate(seed))


@router.delete("/{seed_id}", status_code=204)
async def delete_seed(
    seed_id: uuid.UUID,
    admin: Annotated[object, Depends(require_permission("seed.manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await purchase_service.delete_seed(db, admin=admin, seed_id=seed_id)
