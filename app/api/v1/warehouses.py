import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import require_permission
from app.schemas.common import SuccessResponse
from app.schemas.warehouse import (
    WarehouseCreate,
    WarehouseInventoryAdd,
    WarehouseInventoryPublic,
    WarehousePublic,
    WarehouseSlotCreate,
    WarehouseSlotPublic,
    WarehouseSlotUpdate,
)
from app.services import booking_service

router = APIRouter(prefix="/warehouses", tags=["warehouses"])
slots_router = APIRouter(prefix="/warehouse-slots", tags=["warehouses"])


@router.get("", response_model=SuccessResponse[list[WarehousePublic]])
async def list_warehouses(
    _: Annotated[object, Depends(require_permission("warehouse.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    warehouses = await booking_service.list_warehouses(db)
    return SuccessResponse(data=[WarehousePublic.model_validate(w) for w in warehouses])


@router.post("", response_model=SuccessResponse[WarehousePublic], status_code=201)
async def create_warehouse(
    body: WarehouseCreate,
    actor: Annotated[object, Depends(require_permission("warehouse.create"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    warehouse = await booking_service.create_warehouse(
        db, actor=actor, name=body.name, address=body.address,
        total_capacity_kg=body.total_capacity_kg, manager_id=body.manager_id,
    )
    return SuccessResponse(data=WarehousePublic.model_validate(warehouse))


@router.post(
    "/{warehouse_id}/inventory",
    response_model=SuccessResponse[WarehouseInventoryPublic],
    status_code=201,
)
async def add_inventory(
    warehouse_id: uuid.UUID,
    body: WarehouseInventoryAdd,
    actor: Annotated[object, Depends(require_permission("warehouse.manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    row = await booking_service.add_inventory(
        db, actor=actor, warehouse_id=warehouse_id,
        grain_type=body.grain_type, quantity_kg=body.quantity_kg,
    )
    return SuccessResponse(data=WarehouseInventoryPublic.model_validate(row))


@router.get("/{warehouse_id}/slots", response_model=SuccessResponse[list[WarehouseSlotPublic]])
async def list_slots(
    warehouse_id: uuid.UUID,
    _: Annotated[object, Depends(require_permission("warehouse.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    slots = await booking_service.list_slots(db, warehouse_id=warehouse_id)
    return SuccessResponse(data=[WarehouseSlotPublic.model_validate(s) for s in slots])


@slots_router.get("", response_model=SuccessResponse[list[WarehouseSlotPublic]])
async def list_warehouse_slots(
    actor: Annotated[object, Depends(require_permission("warehouse.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    warehouse_id: uuid.UUID | None = Query(None),
    slot_date: date | None = Query(None, alias="date"),
):
    slots = await booking_service.list_slots_filtered(
        db,
        warehouse_id=warehouse_id,
        slot_date=slot_date,
        include_inactive=actor.role.value != "farmer",
    )
    return SuccessResponse(data=[WarehouseSlotPublic.model_validate(s) for s in slots])


@slots_router.post("", response_model=SuccessResponse[WarehouseSlotPublic], status_code=201)
async def create_warehouse_slot(
    body: WarehouseSlotCreate,
    actor: Annotated[object, Depends(require_permission("warehouse.manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    slot = await booking_service.create_slot(
        db, actor=actor, warehouse_id=body.warehouse_id, slot_date=body.slot_date,
        start_time=body.start_time, end_time=body.end_time, capacity_kg=body.capacity_kg,
    )
    return SuccessResponse(data=WarehouseSlotPublic.model_validate(slot))


@slots_router.patch("/{slot_id}", response_model=SuccessResponse[WarehouseSlotPublic])
async def update_warehouse_slot(
    slot_id: uuid.UUID,
    body: WarehouseSlotUpdate,
    actor: Annotated[object, Depends(require_permission("warehouse.manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    slot = await booking_service.update_slot(
        db, actor=actor, slot_id=slot_id, status=body.status, capacity_kg=body.capacity_kg
    )
    return SuccessResponse(data=WarehouseSlotPublic.model_validate(slot))
