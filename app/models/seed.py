"""
Seeds & Seed Purchases (Master Plan Module 6 — concurrency-critical).

Financial fields use NUMERIC(14,2) throughout (Master Plan §20 / §8) —
never Float — and the service layer must use Python Decimal exclusively.
"""
import uuid
from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import PaymentStatus
from app.models.mixins import CreatedAtOnlyMixin, TimestampMixin, UUIDPKMixin


class Seed(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "seeds"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    crop_type: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    variety: Mapped[str | None] = mapped_column(String(120), nullable=True)
    price_per_kg: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    price_grade_a: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    price_grade_b: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    price_grade_c: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    max_order_quantity_kg: Mapped[float | None] = mapped_column(
        Numeric(14, 2), nullable=True, default=50.0, server_default="50.0"
    )
    stock_kg: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    on_hold_kg: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0, server_default="0")
    old_price: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id", ondelete="SET NULL"), nullable=True
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    warehouse_links: Mapped[list["SeedWarehouse"]] = relationship(
        lazy="raise", back_populates="seed", cascade="all, delete-orphan", passive_deletes=True
    )


class SeedWarehouse(Base, UUIDPKMixin):
    """Which warehouses currently stock/distribute a given seed. Join
    table confirmed to exist in the live schema (migrations reference
    it) but was absent from the outdated base DDL in
    Backend_Technical_Specification.md — see Master Plan §1."""
    __tablename__ = "seed_warehouses"
    __table_args__ = (UniqueConstraint("seed_id", "warehouse_id", name="uq_seed_warehouse"),)

    seed_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("seeds.id", ondelete="CASCADE"), nullable=False
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id", ondelete="CASCADE"), nullable=False
    )

    seed: Mapped["Seed"] = relationship(lazy="raise", back_populates="warehouse_links")


class SeedPurchase(Base, UUIDPKMixin, CreatedAtOnlyMixin):
    __tablename__ = "seed_purchases"
    __table_args__ = (
        Index("ix_seed_purchases_farmer_status", "farmer_id", "payment_status"),
        Index("ix_seed_purchases_status_created", "payment_status", "created_at"),
    )

    farmer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    seed_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("seeds.id"), nullable=False)
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=True
    )
    quantity_kg: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    price_per_kg: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    total_amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    grade: Mapped[str | None] = mapped_column(String(10), nullable=True)

    payment_method: Mapped[str | None] = mapped_column(String(40), nullable=True)
    upi_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    transaction_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    payment_status: Mapped[PaymentStatus] = mapped_column(
        SAEnum(PaymentStatus, name="payment_status", native_enum=False, values_callable=lambda e: [i.value for i in e]),
        default=PaymentStatus.PENDING,
        nullable=False,
    )
    invoice_number: Mapped[str | None] = mapped_column(String(60), unique=True, nullable=True)
    pickup_date: Mapped[date | None] = mapped_column(Date, nullable=True)
