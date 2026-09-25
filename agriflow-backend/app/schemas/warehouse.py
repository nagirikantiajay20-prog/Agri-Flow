import uuid
from datetime import date, time
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.enums import BookingStatus
from app.schemas.common import ORMBase


class WarehousePublic(ORMBase):
    id: uuid.UUID
    name: str
    address: str
    total_capacity_kg: Decimal
    current_load_kg: Decimal
    is_active: bool


class WarehouseSlotPublic(ORMBase):
    id: uuid.UUID
    warehouse_id: uuid.UUID
    slot_date: date
    start_time: time
    end_time: time
    capacity_kg: Decimal
    booked_kg: Decimal
    status: str

    @property
    def remaining_kg(self) -> Decimal:
        return self.capacity_kg - self.booked_kg


class BookingCreate(BaseModel):
    warehouse_id: uuid.UUID
    warehouse_slot_id: uuid.UUID
    booking_date: date
    delivery_address: str = Field(min_length=4)
    grain_type: str = Field(min_length=2, max_length=80)
    quantity_kg: Decimal = Field(gt=0)
    grain_sale_id: uuid.UUID | None = None
    notes: str | None = None


class BookingPublic(ORMBase):
    id: uuid.UUID
    farmer_id: uuid.UUID
    warehouse_id: uuid.UUID
    warehouse_slot_id: uuid.UUID | None
    grain_sale_id: uuid.UUID | None
    booking_date: date
    grain_type: str
    quantity_kg: Decimal
    status: BookingStatus
    notes: str | None


class BookingStatusUpdate(BaseModel):
    status: BookingStatus
    notes: str | None = None


class WarehouseCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    address: str = Field(min_length=4)
    total_capacity_kg: Decimal = Field(gt=0)
    manager_id: uuid.UUID | None = None


class WarehouseInventoryAdd(BaseModel):
    grain_type: str = Field(min_length=2, max_length=80)
    quantity_kg: Decimal = Field(gt=0)


class WarehouseInventoryPublic(ORMBase):
    id: uuid.UUID
    warehouse_id: uuid.UUID
    grain_type: str
    quantity_kg: Decimal


class WarehouseSlotCreate(BaseModel):
    warehouse_id: uuid.UUID
    slot_date: date
    start_time: time
    end_time: time
    capacity_kg: Decimal = Field(gt=0)


class WarehouseSlotUpdate(BaseModel):
    status: str | None = Field(default=None, pattern="^(active|cancelled)$")
    capacity_kg: Decimal | None = Field(default=None, gt=0)
