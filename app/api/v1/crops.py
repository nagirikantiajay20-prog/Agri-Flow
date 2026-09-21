from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import Pagination, require_permission
from app.schemas.common import PaginatedResponse, SuccessResponse
from app.schemas.common import Pagination as PaginationSchema
from app.schemas.crop import CropCreate, CropPublic
from app.services import crop_service

router = APIRouter(prefix="/crops", tags=["crops"])


def _page(rows, total, params):
    total_pages = (total + params.page_size - 1) // params.page_size if total else 0
    return PaginatedResponse(
        data=[CropPublic.model_validate(c) for c in rows],
        pagination=PaginationSchema(
            page=params.page, page_size=params.page_size, total=total, total_pages=total_pages
        ),
    )


@router.get("", response_model=PaginatedResponse[CropPublic])
async def list_crops(
    params: Pagination,
    actor: Annotated[object, Depends(require_permission("crop.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    crops, total = await crop_service.list_crops_for_actor(db, actor=actor, params=params)
    return _page(crops, total, params)


@router.get("/active", response_model=PaginatedResponse[CropPublic])
async def list_active_crops(
    params: Pagination,
    _: Annotated[object, Depends(require_permission("crop.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    crops, total = await crop_service.list_active_crops(db, params=params)
    return _page(crops, total, params)


@router.post("", response_model=SuccessResponse[CropPublic], status_code=201)
async def register_crop(
    body: CropCreate,
    farmer: Annotated[object, Depends(require_permission("crop.create"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    crop = await crop_service.register_crop(
        db, farmer=farmer, crop_type=body.crop_type, acres=body.acres, sowing_date=body.sowing_date
    )
    return SuccessResponse(data=CropPublic.model_validate(crop))
