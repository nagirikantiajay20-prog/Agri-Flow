import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.enums import CropStatus, VisitStatus
from app.schemas.common import ORMBase


class CropCreate(BaseModel):
    crop_type: str = Field(min_length=2, max_length=80)
    acres: Decimal = Field(gt=0)
    sowing_date: date


class CropPublic(ORMBase):
    id: uuid.UUID
    farmer_id: uuid.UUID
    crop_type: str
    acres: Decimal
    sowing_date: date
    harvest_date: date | None
    status: CropStatus
    current_month: int
    created_at: datetime


class FarmVisitPublic(ORMBase):
    id: uuid.UUID
    crop_id: uuid.UUID
    farmer_id: uuid.UUID
    staff_id: uuid.UUID | None
    visit_month: int
    scheduled_date: date | None
    actual_date: date | None
    status: VisitStatus
    verified_acres: Decimal | None
    report: str | None


class VisitScheduleUpdate(BaseModel):
    staff_id: uuid.UUID | None = None
    scheduled_date: date | None = None


class VisitCompleteRequest(BaseModel):
    verified_acres: Decimal = Field(gt=0)
    report: str = Field(min_length=1)
    diagnosis: str | None = None
    recommendation: str | None = None


class FarmVisitCreate(BaseModel):
    crop_id: uuid.UUID
    visit_month: int = Field(ge=1, le=6)
    scheduled_date: date
    staff_id: uuid.UUID | None = None
