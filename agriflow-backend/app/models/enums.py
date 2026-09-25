"""
Explicit enums for every status field (Master Plan §22 / §11).

Values match the CHECK constraints verified in the original
Backend_Technical_Specification.md DDL and the live migrations — see
Master Plan §1 / §1.6. Where the legacy system additionally referenced a
4th `admin` role in agriflow-web/src/app/router/AppRouter.jsx that the
users.role CHECK constraint never actually allowed, we deliberately do
NOT carry that role forward (Master Plan §1.6, "A real RBAC
inconsistency"). Only farmer / manager / super_admin exist here.
"""
import enum


class UserRole(str, enum.Enum):
    FARMER = "farmer"
    MANAGER = "manager"
    SUPER_ADMIN = "super_admin"


class UserStatus(str, enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    REJECTED = "rejected"
    SUSPENDED = "suspended"


class BankStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class CropStatus(str, enum.Enum):
    GROWING = "growing"
    HARVESTED = "harvested"
    FAILED = "failed"
    SOLD = "sold"


class VisitStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    PENDING_REVIEW = "pending_review"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"


class GrainGrade(str, enum.Enum):
    A = "A"
    B = "B"
    C = "C"


class GrainSaleStatus(str, enum.Enum):
    PENDING = "pending"
    RECEIVED = "received"
    APPROVED = "approved"
    REJECTED = "rejected"
    PAID = "paid"


class BookingStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    DELIVERED = "delivered"
    INSPECTED = "inspected"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TransactionDirection(str, enum.Enum):
    CREDIT = "credit"
    DEBIT = "debit"


class TransactionStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class TransactionReferenceType(str, enum.Enum):
    SEED_PURCHASE = "seed_purchase"
    GRAIN_SALE = "grain_sale"
    BILLING = "billing"
    OTHER = "other"


class BankRequestStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class CropStage(str, enum.Enum):
    SOWING = "Sowing"
    GROWING = "Growing"
    MATURITY = "Maturity"
    HARVEST = "Harvest"


class DocumentType(str, enum.Enum):
    AVATAR = "avatar"
    AADHAAR = "aadhaar"
    PASSBOOK = "passbook"
    LAND = "land"


class NotificationType(str, enum.Enum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
