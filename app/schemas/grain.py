import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.enums import GrainGrade, GrainSaleStatus
from app.schemas.common import ORMBase


class GrainSaleCreate(BaseModel):
    grain_type: str = Field(min_length=2, max_length=80)
    grade: GrainGrade
    raw_material_kg: Decimal = Field(gt=0)
    crop_id: uuid.UUID | None = None


class GrainSalePublic(ORMBase):
    id: uuid.UUID
    farmer_id: uuid.UUID
    crop_id: uuid.UUID | None
    grain_type: str
    grade: GrainGrade
    raw_material_kg: Decimal
    wastage_kg: Decimal
    good_material_kg: Decimal
    price_per_kg: Decimal | None
    total_amount: Decimal
    status: GrainSaleStatus
    created_at: datetime


class GrainSaleReviewRequest(BaseModel):
    """Ports the verified inspect_crop -> procure_crop flow (Master Plan
    Module 8): a manager records good/bad quantity from the booking
    slot's crop_inspection, which determines wastage_kg and
    good_material_kg, then the sale moves to `approved`."""
    booking_slot_id: uuid.UUID
    good_quantity_kg: Decimal = Field(ge=0)
    bad_quantity_kg: Decimal = Field(ge=0)
    rejection_reason: str | None = None
    approve: bool = True


class GrainSalePayRequest(BaseModel):
    payment_reference: str | None = None


class CropInspectionPublic(ORMBase):
    id: uuid.UUID
    booking_slot_id: uuid.UUID
    grain_sale_id: uuid.UUID
    inspector_id: uuid.UUID
    good_quantity_kg: Decimal
    bad_quantity_kg: Decimal
    rejection_reason: str | None


class GrainProcureRequest(BaseModel):
    farmer_id: uuid.UUID
    grain_type: str = Field(min_length=2, max_length=80)
    grade: GrainGrade
    raw_material_kg: Decimal = Field(gt=0)
    good_material_kg: Decimal = Field(ge=0)
    wastage_kg: Decimal = Field(ge=0)


class BookingInspectionRequest(BaseModel):
    good_quantity_kg: Decimal = Field(ge=0)
    bad_quantity_kg: Decimal = Field(ge=0)
    rejection_reason: str | None = None
    notes: str | None = None


class GrainYieldUpdateRequest(BaseModel):
    good_material_kg: Decimal = Field(ge=0)
    wastage_kg: Decimal = Field(ge=0)
