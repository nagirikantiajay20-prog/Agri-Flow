import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.farmer._mappers import crop_out, visit_out
from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import require_farmer
from app.integrations import storage
from app.models.crop import Crop
from app.models.enums import CropStatus
from app.models.user import User
from app.schemas import farmer_app as s
from app.schemas.common import MessageResponse, SuccessResponse
from app.services import crop_service

router = APIRouter(prefix="/crops", tags=["farmer · crops"])

MAX_OWN_CROPS = 500
SCAN_TYPES = {"image/jpeg", "image/png"}


@router.get("", response_model=SuccessResponse[list[s.CropOut]])
async def list_crops(
    farmer: Annotated[User, Depends(require_farmer("crop.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    include_closed: bool = Query(
        False, description="Also return harvested / failed / sold / deleted crops (history view)"
    ),
):
    query = select(Crop).where(Crop.farmer_id == farmer.id)
    if not include_closed:
        query = query.where(Crop.status == CropStatus.GROWING, Crop.deleted_at.is_(None))
    crops = (await db.execute(query.order_by(Crop.created_at.desc()).limit(MAX_OWN_CROPS))).scalars().all()
    return SuccessResponse(data=[crop_out(c) for c in crops])


@router.post("", response_model=SuccessResponse[s.CropCreatedOut], status_code=201)
async def register_crop(
    body: s.CropCreateIn,
    farmer: Annotated[User, Depends(require_farmer("crop.create"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    crop = await crop_service.register_crop(
        db,
        farmer=farmer,
        crop_type=body.crop_type,
        crop_name=body.crop_name,
        acres=body.acres,
        sowing_date=body.sowing_date,
        harvest_date=body.harvest_date,
        stage=body.status,
        notes=body.notes or body.location,
    )
    visits = await crop_service.list_own_visits(db, farmer=farmer, crop_id=crop.id)
    return SuccessResponse(
        data=s.CropCreatedOut(crop=crop_out(crop), visits=[visit_out(v) for v in visits]),
        message="Crop registered",
    )


@router.get("/visits", response_model=SuccessResponse[list[s.VisitOut]])
async def list_visits(
    farmer: Annotated[User, Depends(require_farmer("visit.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    crop_id: uuid.UUID | None = Query(None),
):
    visits = await crop_service.list_own_visits(db, farmer=farmer, crop_id=crop_id)
    return SuccessResponse(data=[visit_out(v) for v in visits])


@router.patch("/{crop_id}", response_model=SuccessResponse[s.CropOut])
async def update_crop(
    crop_id: uuid.UUID,
    body: s.CropUpdateIn,
    farmer: Annotated[User, Depends(require_farmer("crop.create"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    updates = body.model_dump(exclude_unset=True)
    if "status" in updates:
        updates["stage"] = updates.pop("status")
    crop = await crop_service.update_own_crop(db, farmer=farmer, crop_id=crop_id, updates=updates)
    return SuccessResponse(data=crop_out(crop), message="Crop updated")


@router.delete("/{crop_id}", response_model=MessageResponse)
async def delete_crop(
    crop_id: uuid.UUID,
    farmer: Annotated[User, Depends(require_farmer("crop.create"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    # Soft delete — see crop_service.delete_own_crop. The crop, its farm
    # visits and any linked grain sales are preserved; only hidden from
    # the default (active) crop list.
    await crop_service.delete_own_crop(db, farmer=farmer, crop_id=crop_id)
    return MessageResponse(message="Crop field removed from your active list")


@router.get("/{crop_id}/inspections", response_model=SuccessResponse[list[s.VisitOut]])
async def crop_inspections(
    crop_id: uuid.UUID,
    farmer: Annotated[User, Depends(require_farmer("visit.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await crop_service.get_own_crop(db, farmer=farmer, crop_id=crop_id)
    visits = await crop_service.list_own_visits(db, farmer=farmer, crop_id=crop_id)
    return SuccessResponse(data=[visit_out(v) for v in visits])


@router.post("/{crop_id}/scan", response_model=SuccessResponse[s.ScanOut], status_code=201)
async def scan_crop(
    crop_id: uuid.UUID,
    farmer: Annotated[User, Depends(require_farmer("crop.create"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    image: UploadFile = File(...),
    notes: str | None = Form(None),
):
    # Reject a deleted crop before spending an upload on it.
    await crop_service.get_own_active_crop(db, farmer=farmer, crop_id=crop_id)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    stored = await storage.upload_bytes(
        bucket_key="crop_scans",
        file_name=image.filename or "scan.jpg",
        content_type=image.content_type or "application/octet-stream",
        data=await image.read(),
        object_name=f"farmer_{farmer.id}/{crop_id}_{stamp}",
        max_size_mb=settings.UPLOAD_MAX_SIZE_MB,
        allowed_content_types=SCAN_TYPES,
    )
    visit = await crop_service.submit_scan(
        db, farmer=farmer, crop_id=crop_id, image_path=stored["object_path"], notes=notes
    )
    return SuccessResponse(
        data=s.ScanOut(inspection=visit_out(visit), image_url=storage.read_url(stored["object_path"])),
        message="Scan submitted — an officer will review it",
    )
