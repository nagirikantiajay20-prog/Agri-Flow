"""
Canonical identity model (Master Plan §6, Module 1).

Replaces the current dual auth.users / profiles / users(legacy int PK)
design. This is the TARGET schema for the new backend, not a literal
mirror of today's tables — cutting over real data from the legacy model
into this one is explicit migration work (Master Plan §9 Phase 3/4), not
something this model file does implicitly. A `legacy_app_user_id` bridge
column is kept during that migration and can be dropped once cutover is
verified complete.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import BankStatus, DocumentType, UserRole, UserStatus
from app.models.mixins import CreatedAtOnlyMixin, TimestampMixin, UUIDPKMixin


class User(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_role_status", "role", "status"),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    phone: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, name="user_role", native_enum=False, values_callable=lambda e: [i.value for i in e]),
        nullable=False,
    )
    status: Mapped[UserStatus] = mapped_column(
        SAEnum(UserStatus, name="user_status", native_enum=False, values_callable=lambda e: [i.value for i in e]),
        nullable=False,
        default=UserStatus.PENDING,
    )
    first_login: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Bridge column for the legacy `users.id BIGSERIAL` during cutover
    # (Master Plan §6). Nullable; only populated for accounts migrated
    # from the old schema. Not used by any new code path.
    legacy_app_user_id: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True)

    farmer_profile: Mapped["FarmerProfile | None"] = relationship(
        lazy="raise", back_populates="user", uselist=False,
        cascade="all, delete-orphan", passive_deletes=True,
    )
    staff_profile: Mapped["StaffProfile | None"] = relationship(
        lazy="raise", back_populates="user", uselist=False,
        cascade="all, delete-orphan", passive_deletes=True,
    )
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        lazy="raise", back_populates="user",
        cascade="all, delete-orphan", passive_deletes=True,
    )


class FarmerProfile(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "farmer_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    farm_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    village: Mapped[str | None] = mapped_column(String(100), nullable=True)
    district: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    acres_of_land: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    crop_address: Mapped[str | None] = mapped_column(Text, nullable=True)

    bank_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    account_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ifsc_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    upi_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    bank_status: Mapped[BankStatus] = mapped_column(
        SAEnum(BankStatus, name="bank_status", native_enum=False, values_callable=lambda e: [i.value for i in e]),
        default=BankStatus.APPROVED,
        nullable=False,
    )

    profile_photo: Mapped[str | None] = mapped_column(Text, nullable=True)
    soil_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    irrigation_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    primary_crop: Mapped[str | None] = mapped_column(String(100), nullable=True)
    secondary_crop: Mapped[str | None] = mapped_column(String(100), nullable=True)
    aadhaar_card_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    bank_passbook_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    land_ownership_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship(lazy="raise", back_populates="farmer_profile")


class StaffProfile(Base, UUIDPKMixin):
    """Covers both `manager` and `super_admin` roles — replaces the
    legacy `admin_profiles` table name, which was confusing given the
    system also has a (dead-code, DB-constraint-illegal) `admin` role
    reference in the frontend router (Master Plan §1.6)."""
    __tablename__ = "staff_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    department: Mapped[str | None] = mapped_column(String(120), nullable=True)
    assigned_region: Mapped[str | None] = mapped_column(String(120), nullable=True)

    user: Mapped["User"] = relationship(lazy="raise", back_populates="staff_profile")


class RefreshToken(Base, UUIDPKMixin, CreatedAtOnlyMixin):
    """Opaque refresh tokens are stored hashed, never in plaintext.
    Rotated on every use (app.services.auth_service) — the old row is
    marked revoked rather than deleted, so reuse of a stolen/rotated
    token can be detected and the whole family revoked."""
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("ix_refresh_tokens_user_revoked", "user_id", "revoked"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    replaced_by_token_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)

    user: Mapped["User"] = relationship(lazy="raise", back_populates="refresh_tokens")


class FarmerDocument(Base, UUIDPKMixin, CreatedAtOnlyMixin):
    __tablename__ = "farmer_documents"
    __table_args__ = (Index("ix_farmer_documents_farmer_type", "farmer_id", "document_type"),)

    farmer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    document_type: Mapped[DocumentType] = mapped_column(
        SAEnum(DocumentType, name="document_type", native_enum=False, values_callable=lambda e: [i.value for i in e]),
        nullable=False,
    )
    object_path: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)


class FcmDeviceToken(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "fcm_device_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fcm_token: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    device_type: Mapped[str] = mapped_column(String(20), default="android", nullable=False)
