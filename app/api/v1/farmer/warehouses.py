import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.farmer._mappers import slot_out, warehouse_out
from app.core.database import get_db
from app.core.dependencies import require_farmer
from app.models.user import User
from app.schemas import farmer_app as s
from app.schemas.common import SuccessResponse
from app.services import booking_service

router = APIRouter(prefix="/warehouses", tags=["farmer · warehouses"])


@router.get("", response_model=SuccessResponse[list[s.WarehouseOut]])
async def list_warehouses(
    _: Annotated[User, Depends(require_farmer("warehouse.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    warehouses = await booking_service.list_warehouses(db)
    return SuccessResponse(data=[warehouse_out(w) for w in sorted(warehouses, key=lambda w: w.name)])


@router.get("/{warehouse_id}/slots", response_model=SuccessResponse[list[s.SlotOut]])
async def list_slots(
    warehouse_id: uuid.UUID,
    _: Annotated[User, Depends(require_farmer("warehouse.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    slot_date: date | None = Query(None, alias="date", description="YYYY-MM-DD; omit for all upcoming slots"),
):
    slots = await booking_service.list_slots_filtered(db, warehouse_id=warehouse_id, slot_date=slot_date)
    today = date.today()
    upcoming = sorted(
        (x for x in slots if x.slot_date >= today),
        key=lambda x: (x.slot_date, x.start_time),
    )
    return SuccessResponse(data=[slot_out(x) for x in upcoming])
