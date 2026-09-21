import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.enums import PaymentStatus
from app.schemas.common import ORMBase


class SeedPublic(ORMBase):
    id: uuid.UUID
    name: str
    variety: str | None
    price_per_kg: Decimal
    stock_kg: Decimal
    description: str | None
    image_url: str | None
    is_active: bool


class SeedCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    variety: str | None = None
    price_per_kg: Decimal = Field(gt=0)
    stock_kg: Decimal = Field(ge=0)
    description: str | None = None
    image_url: str | None = None


class SeedUpdate(BaseModel):
    price_per_kg: Decimal | None = Field(default=None, gt=0)
    stock_kg: Decimal | None = Field(default=None, ge=0)
    description: str | None = None
    is_active: bool | None = None


class SeedPurchaseCreate(BaseModel):
    seed_id: uuid.UUID
    quantity_kg: Decimal = Field(gt=0)
    warehouse_id: uuid.UUID | None = None
    payment_method: str | None = None
    upi_id: str | None = None


class SeedPurchasePublic(ORMBase):
    id: uuid.UUID
    farmer_id: uuid.UUID
    seed_id: uuid.UUID
    quantity_kg: Decimal
    price_per_kg: Decimal
    total_amount: Decimal
    payment_status: PaymentStatus
    invoice_number: str | None
    created_at: datetime


class SeedPurchaseStatusUpdate(BaseModel):
    payment_status: PaymentStatus
