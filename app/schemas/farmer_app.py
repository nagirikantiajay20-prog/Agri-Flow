"""
Request/response contract for the farmer Android app (/api/v1/farmer).

Field names follow docs/FARMER_APP_INTEGRATION.md. Money and weights are
serialised as JSON numbers because that is what the Flutter models parse;
all arithmetic behind them stays in Decimal. IDs are UUID strings.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, PlainSerializer, field_validator

from app.models.enums import (
    BookingStatus,
    CropStage,
    DocumentType,
    GrainGrade,
    NotificationType,
    PaymentStatus,
    TransactionDirection,
    TransactionReferenceType,
    TransactionStatus,
    UserRole,
    UserStatus,
    VisitStatus,
)

Number = Annotated[Decimal, PlainSerializer(float, return_type=float, when_used="json")]

_IFSC = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")


def normalize_phone(raw: str) -> str:
    """Accepts 9502662924, +91 95026 62924, 09502662924 and returns the
    10-digit national number every account is stored under."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if not re.fullmatch(r"\d{10}", digits):
        raise ValueError("Enter a valid 10-digit mobile number")
    return digits


def mask_account(number: str | None) -> str | None:
    if not number:
        return number
    return "*" * max(0, len(number) - 4) + number[-4:]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ── Auth ────────────────────────────────────────────────────────────────
class FarmerLoginRequest(BaseModel):
    phone: str
    password: str = Field(min_length=6, max_length=128)

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return normalize_phone(v)


class FarmerRefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=10)


class FarmerLogoutRequest(BaseModel):
    fcm_token: str | None = None


class FarmerUser(BaseModel):
    id: uuid.UUID
    name: str
    phone: str
    email: str | None
    role: UserRole
    status: UserStatus


class FarmerProfileOut(BaseModel):
    farmer_id: uuid.UUID
    name: str
    phone: str
    email: str | None
    status: UserStatus
    farm_name: str | None
    address: str | None
    village: str | None
    district: str | None
    state: str | None
    crop_address: str | None
    acres_of_land: Number
    soil_type: str | None
    irrigation_type: str | None
    primary_crop: str | None
    secondary_crop: str | None
    bank_name: str | None
    bank_account_number: str | None = Field(description="Masked — only the last 4 digits are visible")
    bank_ifsc: str | None
    upi_id: str | None
    bank_status: str
    avatar_url: str | None
    aadhaar_url: str | None = Field(description="Short-lived signed URL")
    passbook_url: str | None = Field(description="Short-lived signed URL")
    land_proof_url: str | None = Field(description="Short-lived signed URL")


class FarmerLoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: FarmerUser
    farmer_profile: FarmerProfileOut


class FarmerTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


# ── Profile ─────────────────────────────────────────────────────────────
class FarmerProfileUpdate(_Strict):
    name: str | None = Field(default=None, min_length=2, max_length=100)
    email: EmailStr | None = None
    farm_name: str | None = Field(default=None, max_length=100)
    address: str | None = None
    village: str | None = Field(default=None, max_length=100)
    district: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    crop_address: str | None = None
    acres_of_land: Decimal | None = Field(default=None, ge=0, le=100000)
    soil_type: str | None = Field(default=None, max_length=80)
    irrigation_type: str | None = Field(default=None, max_length=80)
    primary_crop: str | None = Field(default=None, max_length=100)
    secondary_crop: str | None = Field(default=None, max_length=100)


class BankChangeIn(BaseModel):
    bank_name: str = Field(min_length=2, max_length=100)
    account_number: str = Field(pattern=r"^\d{9,18}$")
    ifsc_code: str
    upi_id: str | None = Field(default=None, max_length=100)

    @field_validator("ifsc_code")
    @classmethod
    def _ifsc(cls, v: str) -> str:
        v = v.strip().upper()
        if not _IFSC.match(v):
            raise ValueError("IFSC must look like SBIN0001234")
        return v


class BankChangeOut(BaseModel):
    request_id: uuid.UUID
    status: str


# ── Seeds ───────────────────────────────────────────────────────────────
class SeedOut(BaseModel):
    id: uuid.UUID
    name: str
    crop_type: str | None
    variety: str | None
    description: str | None
    price_per_kg: Number
    old_price: Number | None
    stock_kg: Number
    image_url: str | None
    warehouse_id: uuid.UUID | None


class SeedPurchaseIn(BaseModel):
    seed_id: uuid.UUID
    quantity_kg: Decimal = Field(gt=0, le=100000)
    grade: GrainGrade = GrainGrade.A
    warehouse_id: uuid.UUID | None = None
    pickup_date: date | None = None
    payment_method: str = Field(default="warehouse", max_length=40)

    @field_validator("pickup_date")
    @classmethod
    def _not_in_past(cls, v: date | None) -> date | None:
        if v is not None and v < date.today():
            raise ValueError("pickup_date cannot be in the past")
        return v


class SeedPurchaseReceipt(BaseModel):
    order_id: uuid.UUID
    invoice_number: str
    seed_name: str
    quantity_kg: Number
    grade: str | None
    warehouse_id: uuid.UUID | None
    warehouse_name: str | None
    pickup_date: date | None
    price_per_kg: Number
    total_amount: Number
    payment_status: PaymentStatus
    payment_status_label: str


class SeedPurchaseOut(BaseModel):
    id: uuid.UUID
    invoice_number: str | None
    quantity_kg: Number
    price_per_kg: Number
    total_amount: Number
    grade: str | None
    payment_status: PaymentStatus
    pickup_date: date | None
    warehouse_id: uuid.UUID | None
    created_at: datetime
    seed: SeedOut | None


# ── Crops & visits ──────────────────────────────────────────────────────
class CropOut(BaseModel):
    id: uuid.UUID
    farmer_id: uuid.UUID
    crop_name: str | None
    crop_type: str
    acres: Number
    sowing_date: date
    harvest_date: date | None
    status: CropStage = Field(description="Growth stage shown in the app")
    lifecycle_status: str = Field(description="growing | harvested | failed | sold")
    notes: str | None
    created_at: datetime


class CropCreateIn(BaseModel):
    crop_name: str | None = Field(default=None, max_length=100)
    crop_type: str = Field(min_length=2, max_length=80)
    acres: Decimal = Field(gt=0, le=100000)
    sowing_date: date
    harvest_date: date | None = None
    status: CropStage = CropStage.SOWING
    notes: str | None = None
    location: str | None = None


class CropUpdateIn(_Strict):
    crop_name: str | None = Field(default=None, max_length=100)
    crop_type: str | None = Field(default=None, min_length=2, max_length=80)
    acres: Decimal | None = Field(default=None, gt=0, le=100000)
    sowing_date: date | None = None
    harvest_date: date | None = None
    status: CropStage | None = None
    notes: str | None = None


class VisitOut(BaseModel):
    id: uuid.UUID
    crop_id: uuid.UUID
    visit_month: int
    visit_date: date | None
    scheduled_date: date | None
    status: VisitStatus
    notes: str | None
    report: str | None
    diagnosis: str | None
    recommendation: str | None
    image_url: str | None


class CropCreatedOut(BaseModel):
    crop: CropOut
    visits: list[VisitOut]


class ScanOut(BaseModel):
    inspection: VisitOut
    image_url: str | None


# ── Warehouses & bookings ───────────────────────────────────────────────
class WarehouseOut(BaseModel):
    id: uuid.UUID
    name: str
    address: str
    location: str | None
    contact_number: str | None
    capacity: Number
    available_capacity: Number


class SlotOut(BaseModel):
    id: uuid.UUID
    warehouse_id: uuid.UUID
    slot_date: date
    slot_time: str
    start_time: time
    end_time: time
    total_capacity_kg: Number
    available_weight_kg: Number
    available_weight_qtl: Number
    max_bookings: int
    available_bookings: int
    status: str


class BookSlotIn(BaseModel):
    warehouse_id: uuid.UUID
    warehouse_slot_id: uuid.UUID
    grain_type: str = Field(min_length=2, max_length=80)
    quantity_kg: Decimal = Field(gt=0, le=10000000)
    booking_date: date
    delivery_address: str = Field(min_length=4)
    grain_sale_id: uuid.UUID | None = None
    notes: str | None = None


class BookSlotOut(BaseModel):
    booking_id: uuid.UUID
    status: BookingStatus


class WarehouseBrief(BaseModel):
    id: uuid.UUID
    name: str
    address: str
    contact_number: str | None


class BookingOut(BaseModel):
    id: uuid.UUID
    grain_type: str
    quantity_kg: Number
    booking_date: date
    delivery_address: str
    status: BookingStatus
    notes: str | None
    warehouse_slot_id: uuid.UUID | None
    slot_time: str | None
    created_at: datetime
    warehouse: WarehouseBrief | None


class GrainOfferIn(BaseModel):
    crop_type: str = Field(min_length=2, max_length=80)
    grade: GrainGrade = GrainGrade.A
    quantity_kg: Decimal = Field(gt=0, le=10000000)
    price_per_kg: Decimal | None = Field(default=None, gt=0, description="The farmer's asking price")
    notes: str | None = None
    crop_id: uuid.UUID | None = None


class GrainOfferOut(BaseModel):
    id: uuid.UUID
    crop_type: str
    grade: GrainGrade
    quantity_kg: Number
    offered_price_per_kg: Number | None
    price_per_kg: Number | None = Field(description="Final price, set at review")
    good_material_kg: Number
    wastage_kg: Number
    total_amount: Number
    status: str
    notes: str | None
    created_at: datetime


# ── Notifications ───────────────────────────────────────────────────────
class NotificationOut(BaseModel):
    id: uuid.UUID
    title: str
    message: str
    type: NotificationType
    is_read: bool
    reference_type: str | None
    reference_id: uuid.UUID | None
    created_at: datetime


class UnreadCountOut(BaseModel):
    unread_count: int


class FcmTokenIn(BaseModel):
    fcm_token: str = Field(min_length=20, max_length=4096)
    device_type: str = Field(default="android", pattern="^(android|ios)$")


# ── Ledger & market ─────────────────────────────────────────────────────
class TransactionOut(BaseModel):
    id: uuid.UUID
    reference_type: TransactionReferenceType
    reference_id: uuid.UUID | None
    amount: Number
    direction: TransactionDirection
    status: TransactionStatus
    description: str | None
    invoice_number: str | None
    transaction_id: str | None
    created_at: datetime


class MarketRateOut(BaseModel):
    crop_type: str
    grade: GrainGrade
    variety: str | None
    price_per_kg: Number
    price_per_qtl: Number
    change_percentage: Number
    effective_date: date


# ── Dashboard ───────────────────────────────────────────────────────────
class WeatherOut(BaseModel):
    temperature_c: int
    feels_like_c: int
    condition: str
    advisory: str
    humidity_percent: int
    wind_kmh: int


class DashboardCrop(BaseModel):
    id: uuid.UUID
    crop_name: str | None
    crop_type: str
    acres: Number
    stage: CropStage
    stage_progress_percent: int
    health_status: str


class DashboardOrder(BaseModel):
    id: uuid.UUID
    reference: str
    type: str
    title: str
    date: date
    status: str
    amount: Number


class DashboardOut(BaseModel):
    farmer_id: uuid.UUID
    farmer_name: str
    farm_name: str | None
    active_crops_count: int
    total_acres: Number
    pending_bookings: int
    recent_earnings: Number
    unread_notifications: int
    weather: WeatherOut | None
    crops: list[DashboardCrop]
    mandi_prices: list[MarketRateOut]
    recent_orders: list[DashboardOrder]


# ── Documents ───────────────────────────────────────────────────────────
class DocumentUploadOut(BaseModel):
    document_id: uuid.UUID
    document_type: DocumentType
    url: str | None = Field(description="Short-lived signed URL (public for avatars when S3_PUBLIC_BASE_URL is set)")
    content_type: str
    size_bytes: int
