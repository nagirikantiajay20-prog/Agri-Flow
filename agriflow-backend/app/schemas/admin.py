import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.enums import (
    GrainGrade,
    NotificationType,
    TransactionDirection,
    TransactionReferenceType,
    TransactionStatus,
    UserRole,
    UserStatus,
)
from app.schemas.common import ORMBase


class TransactionPublic(ORMBase):
    id: uuid.UUID
    reference_type: TransactionReferenceType
    reference_id: uuid.UUID | None
    farmer_id: uuid.UUID
    amount: Decimal
    direction: TransactionDirection
    status: TransactionStatus
    description: str | None
    invoice_number: str | None
    created_at: datetime


class NotificationPublic(ORMBase):
    id: uuid.UUID
    title: str
    message: str
    type: NotificationType
    is_read: bool
    reference_type: str | None
    reference_id: uuid.UUID | None
    created_at: datetime


class MarketRateCreate(BaseModel):
    crop_type: str = Field(min_length=2, max_length=80)
    grade: GrainGrade
    variety: str | None = Field(default=None, max_length=100)
    price_per_kg: Decimal = Field(gt=0)
    effective_date: date


class MarketRatePublic(ORMBase):
    id: uuid.UUID
    crop_type: str
    grade: GrainGrade
    price_per_kg: Decimal
    effective_date: date


class ManagerCreate(BaseModel):
    """Managers/super_admins are created here, by an existing
    super_admin — never through public self-registration (mirrors the
    verified correct pattern in admin-create-user, Master Plan Module
    1/11)."""
    name: str = Field(min_length=2, max_length=255)
    phone: str = Field(min_length=8, max_length=20)
    email: str | None = None
    password: str = Field(min_length=8, max_length=128)
    role: UserRole = UserRole.MANAGER
    assigned_region: str | None = None
    department: str | None = None


class ManagerPublic(ORMBase):
    id: uuid.UUID
    name: str
    phone: str
    email: str | None
    role: UserRole
    status: UserStatus
    created_at: datetime


class ManagerStatusUpdate(BaseModel):
    status: UserStatus


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=8, max_length=128)


class AuditLogPublic(ORMBase):
    id: uuid.UUID
    user_id: uuid.UUID | None
    action: str
    entity_type: str | None
    entity_id: uuid.UUID | None
    old_value: dict | None = None
    new_value: dict | None = None
    details: str | None
    created_at: datetime


class AdminDashboardResponse(BaseModel):
    total_farmers: int
    active_farmers: int
    pending_farmer_approvals: int
    total_managers: int
    active_bookings: int
    pending_grain_sales: int
    total_seed_stock_kg: Decimal
    warehouse_inventory_kg: Decimal
    procurement_mtd_kg: Decimal
    revenue_mtd: Decimal
    procurement_cost_mtd: Decimal
    profit_mtd: Decimal
    pending_payments: int
    active_crops: int
    visits_today: int


class MonthlyReportResponse(BaseModel):
    month: str
    total_seed_purchases_amount: Decimal
    total_grain_sales_amount: Decimal
    total_bookings: int
    new_farmers: int


class TransactionPayRequest(BaseModel):
    description: str | None = None


class PublicStatsResponse(BaseModel):
    farmers: int
    crops: int
    seeds: int
    warehouses: int
