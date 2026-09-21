"""
Ledger, Bank Change Requests, Notifications, Audit Logs, Market Rates
(Master Plan Modules 4, 9, 10, 11).

`Transaction` rows are only ever created as a side effect of
seed-purchase / grain-sale service transactions (Module 9) — there is
deliberately no public "create transaction" endpoint, closing off the
class of abuse possible today where agriflow-web's ledgerService.js
writes `transactions` directly from the frontend.
"""
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Numeric, String, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import (
    BankRequestStatus,
    GrainGrade,
    NotificationType,
    TransactionDirection,
    TransactionReferenceType,
    TransactionStatus,
)
from app.models.mixins import CreatedAtOnlyMixin, UUIDPKMixin


class Transaction(Base, UUIDPKMixin, CreatedAtOnlyMixin):
    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_transactions_farmer_created", "farmer_id", "created_at"),
        Index("ix_transactions_status", "status"),
        Index("ix_transactions_reference", "reference_type", "reference_id"),
    )

    reference_type: Mapped[TransactionReferenceType] = mapped_column(
        SAEnum(
            TransactionReferenceType,
            name="transaction_reference_type",
            native_enum=False,
            values_callable=lambda e: [i.value for i in e],
        ),
        nullable=False,
    )
    reference_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    farmer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    upi_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    transaction_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    direction: Mapped[TransactionDirection] = mapped_column(
        SAEnum(
            TransactionDirection, name="transaction_direction", native_enum=False,
            values_callable=lambda e: [i.value for i in e],
        ),
        nullable=False,
    )
    status: Mapped[TransactionStatus] = mapped_column(
        SAEnum(
            TransactionStatus, name="transaction_status", native_enum=False,
            values_callable=lambda e: [i.value for i in e],
        ),
        default=TransactionStatus.PENDING,
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    invoice_number: Mapped[str | None] = mapped_column(String(60), nullable=True)


class BankChangeRequest(Base, UUIDPKMixin):
    __tablename__ = "bank_change_requests"
    __table_args__ = (
        Index("ix_bank_change_requests_status_requested", "status", "requested_at"),
        Index("ix_bank_change_requests_farmer_status", "farmer_id", "status"),
    )

    farmer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    bank_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    account_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ifsc_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    upi_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[BankRequestStatus] = mapped_column(
        SAEnum(
            BankRequestStatus, name="bank_request_status", native_enum=False,
            values_callable=lambda e: [i.value for i in e],
        ),
        default=BankRequestStatus.PENDING,
        nullable=False,
    )
    admin_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)


class Notification(Base, UUIDPKMixin, CreatedAtOnlyMixin):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_read", "user_id", "is_read"),
        Index("ix_notifications_user_created", "user_id", "created_at"),
        Index("ix_notifications_reference", "reference_type", "reference_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[NotificationType] = mapped_column(
        SAEnum(NotificationType, name="notification_type", native_enum=False, values_callable=lambda e: [i.value for i in e]),
        default=NotificationType.INFO,
        nullable=False,
    )
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reference_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    reference_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # Locale key for future i18n-aware notification rendering
    # (Master Plan §1.6 addendum — Sec 10). Optional, defaults to English.
    locale: Mapped[str] = mapped_column(String(8), default="en", nullable=False)


class AuditLog(Base, UUIDPKMixin, CreatedAtOnlyMixin):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_created", "created_at"),
        Index("ix_audit_logs_user_created", "user_id", "created_at"),
        Index("ix_audit_logs_entity", "entity_type", "entity_id"),
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    old_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    new_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class MarketRate(Base, UUIDPKMixin, CreatedAtOnlyMixin):
    __tablename__ = "market_rates"
    __table_args__ = (
        Index("ix_market_rates_lookup", "crop_type", "grade", "effective_date"),
    )

    crop_type: Mapped[str] = mapped_column(String(80), nullable=False)
    grade: Mapped[GrainGrade] = mapped_column(
        SAEnum(GrainGrade, name="market_rate_grade", native_enum=False, values_callable=lambda e: [i.value for i in e]),
        nullable=False,
    )
    price_per_kg: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    set_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
