import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.enums import PaymentStatus
from app.schemas.common import ORMBase
from app.schemas.warehouse import WarehouseBrief


class SeedWarehouseAssign(BaseModel):
    warehouse_ids: list[uuid.UUID] = Field(default_factory=list)


class SeedPublic(ORMBase):
    id: uuid.UUID
    name: str
    crop_type: str | None = None
    variety: str | None = None
    price_per_kg: Decimal
    price_grade_a: Decimal | None = None
    price_grade_b: Decimal | None = None
    price_grade_c: Decimal | None = None
    max_order_quantity_kg: Decimal | None = None
    stock_kg: Decimal
    warehouse_id: uuid.UUID | None = None
    warehouse_ids: list[uuid.UUID] = Field(default_factory=list)
    warehouses: list[WarehouseBrief] = Field(default_factory=list)
    description: str | None = None
    image_url: str | None = None
    is_active: bool


class SeedCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    crop_type: str | None = None
    variety: str | None = None
    price_per_kg: Decimal = Field(gt=0)
    price_grade_a: Decimal | None = Field(default=None, gt=0)
    price_grade_b: Decimal | None = Field(default=None, gt=0)
    price_grade_c: Decimal | None = Field(default=None, gt=0)
    max_order_quantity_kg: Decimal | None = Field(default=None, gt=0)
    stock_kg: Decimal = Field(ge=0)
    warehouse_id: uuid.UUID | None = None
    warehouse_ids: list[uuid.UUID] = Field(default_factory=list)
    description: str | None = None
    image_url: str | None = None


class SeedUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    crop_type: str | None = None
    variety: str | None = None
    price_per_kg: Decimal | None = Field(default=None, gt=0)
    price_grade_a: Decimal | None = Field(default=None, gt=0)
    price_grade_b: Decimal | None = Field(default=None, gt=0)
    price_grade_c: Decimal | None = Field(default=None, gt=0)
    max_order_quantity_kg: Decimal | None = Field(default=None, gt=0)
    stock_kg: Decimal | None = Field(default=None, ge=0)
    warehouse_id: uuid.UUID | None = None
    warehouse_ids: list[uuid.UUID] | None = None
    description: str | None = None
    image_url: str | None = None
    is_active: bool | None = None


class SeedPurchaseCreate(BaseModel):
    seed_id: uuid.UUID
    quantity_kg: Decimal = Field(gt=0)
    warehouse_id: uuid.UUID | None = None
    grade: str | None = None
    payment_method: str | None = None
    upi_id: str | None = None


class SeedPurchasePublic(ORMBase):
    id: uuid.UUID
    farmer_id: uuid.UUID
    seed_id: uuid.UUID
    warehouse_id: uuid.UUID | None = None
    quantity_kg: Decimal
    price_per_kg: Decimal
    total_amount: Decimal
    grade: str | None = None
    payment_status: PaymentStatus
    invoice_number: str | None = None
    created_at: datetime


class SeedPurchaseStatusUpdate(BaseModel):
    payment_status: PaymentStatus
