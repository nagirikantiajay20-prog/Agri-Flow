import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models.enums import BankRequestStatus, BankStatus, UserStatus
from app.schemas.admin import TransactionPublic
from app.schemas.common import ORMBase
from app.schemas.crop import CropPublic, FarmVisitPublic


class FarmerProfilePublic(ORMBase):
    id: uuid.UUID
    user_id: uuid.UUID
    address: str | None
    acres_of_land: Decimal
    bank_name: str | None
    account_number: str | None
    ifsc_code: str | None
    upi_id: str | None
    bank_status: BankStatus
    soil_type: str | None
    irrigation_type: str | None
    primary_crop: str | None
    secondary_crop: str | None


class UpdateFarmerProfileRequest(BaseModel):
    address: str | None = None
    acres_of_land: Decimal | None = Field(default=None, ge=0)
    soil_type: str | None = None
    irrigation_type: str | None = None
    primary_crop: str | None = None
    secondary_crop: str | None = None


class BankChangeRequestCreate(BaseModel):
    bank_name: str = Field(min_length=2, max_length=255)
    account_number: str = Field(min_length=4, max_length=64)
    ifsc_code: str = Field(min_length=4, max_length=20)
    upi_id: str | None = None


class BankChangeRequestPublic(ORMBase):
    id: uuid.UUID
    farmer_id: uuid.UUID
    bank_name: str | None
    account_number: str | None
    ifsc_code: str | None
    upi_id: str | None
    status: BankRequestStatus
    admin_notes: str | None
    requested_at: datetime
    reviewed_at: datetime | None


class BankChangeReviewRequest(BaseModel):
    approve: bool
    admin_notes: str | None = None


class FarmerApprovalRequest(BaseModel):
    status: UserStatus

    def validate_transition(self) -> None:
        if self.status not in (UserStatus.ACTIVE, UserStatus.REJECTED, UserStatus.SUSPENDED):
            raise ValueError("Farmer approval can only set active / rejected / suspended")


class FarmerAdminView(ORMBase):
    id: uuid.UUID
    name: str
    phone: str
    email: str | None
    status: UserStatus
    created_at: datetime
    farmer_profile: FarmerProfilePublic | None


class FarmerCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    phone: str = Field(min_length=8, max_length=20)

    @field_validator("phone")
    @classmethod
    def _normalize_phone(cls, v: str) -> str:
        from app.schemas.farmer_app import normalize_phone

        return normalize_phone(v)

    email: EmailStr | None = None
    password: str = Field(min_length=8, max_length=128)
    address: str | None = None
    acres_of_land: Decimal | None = Field(default=None, ge=0)
    crop_address: str | None = None


class FarmerAdminUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    phone: str | None = Field(default=None, min_length=8, max_length=20)

    @field_validator("phone")
    @classmethod
    def _normalize_phone(cls, v: str | None) -> str | None:
        if v is not None:
            from app.schemas.farmer_app import normalize_phone

            return normalize_phone(v)
        return v

    email: EmailStr | None = None
    address: str | None = None
    farm_name: str | None = Field(default=None, max_length=100)
    village: str | None = Field(default=None, max_length=100)
    district: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    acres_of_land: Decimal | None = Field(default=None, ge=0)
    crop_address: str | None = None
    soil_type: str | None = Field(default=None, max_length=80)
    irrigation_type: str | None = Field(default=None, max_length=80)
    primary_crop: str | None = Field(default=None, max_length=100)
    secondary_crop: str | None = Field(default=None, max_length=100)


class FarmerDashboardResponse(BaseModel):
    active_crops: int
    total_earned: Decimal
    total_spent: Decimal
    unread_notifications: int
    recent_crops: list[CropPublic]
    recent_transactions: list[TransactionPublic]
    upcoming_visits: list[FarmVisitPublic]
