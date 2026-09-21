"""
Warehouses & Bookings (Master Plan Module 7 — the highest-concurrency-risk
module in the system; confirmed FOR UPDATE row locking in the legacy
`create_booking_slot` / `procure_grain_booking` RPCs must be preserved
exactly by the service layer that uses these models. See
app/services/booking_service.py.
"""
import uuid
from datetime import date, time

from sqlalchemy import Boolean, Date, ForeignKey, Index, Numeric, String, Text, Time, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import BookingStatus
from app.models.mixins import CreatedAtOnlyMixin, UUIDPKMixin


class Warehouse(Base, UUIDPKMixin, CreatedAtOnlyMixin):
    __tablename__ = "warehouses"

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    total_capacity_kg: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    current_load_kg: Mapped[float] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    manager_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    slots: Mapped[list["WarehouseSlot"]] = relationship(
        lazy="raise", back_populates="warehouse", cascade="all, delete-orphan", passive_deletes=True
    )
    inventory: Mapped[list["WarehouseInventory"]] = relationship(
        lazy="raise", back_populates="warehouse", cascade="all, delete-orphan", passive_deletes=True
    )


class WarehouseSlot(Base, UUIDPKMixin, CreatedAtOnlyMixin):
    """Bookable delivery time-slots per warehouse per date. Confirmed to
    exist in the live schema (referenced throughout farmer-api /
    manager-api and altered in migrations) but absent from the outdated
    base DDL — see Master Plan §1."""
    __tablename__ = "warehouse_slots"
    __table_args__ = (
        Index("ix_warehouse_slots_wh_date_status", "warehouse_id", "slot_date", "status"),
    )

    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slot_date: Mapped[date] = mapped_column(Date, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    capacity_kg: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    booked_kg: Mapped[float] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)

    warehouse: Mapped["Warehouse"] = relationship(lazy="raise", back_populates="slots")
    bookings: Mapped[list["BookingSlot"]] = relationship(lazy="raise", back_populates="warehouse_slot")


class WarehouseInventory(Base, UUIDPKMixin):
    __tablename__ = "warehouse_inventory"
    __table_args__ = (UniqueConstraint("warehouse_id", "grain_type", name="uq_warehouse_grain"),)

    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id", ondelete="CASCADE"), nullable=False
    )
    grain_type: Mapped[str] = mapped_column(String(80), nullable=False)
    quantity_kg: Mapped[float] = mapped_column(Numeric(14, 2), default=0, nullable=False)

    warehouse: Mapped["Warehouse"] = relationship(lazy="raise", back_populates="inventory")


class BookingSlot(Base, UUIDPKMixin, CreatedAtOnlyMixin):
    __tablename__ = "booking_slots"
    __table_args__ = (
        Index("ix_booking_slots_farmer_status", "farmer_id", "status"),
        Index("ix_booking_slots_slot_status", "warehouse_slot_id", "status"),
        Index("ix_booking_slots_status_created", "status", "created_at"),
    )

    farmer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    grain_sale_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("grain_sales.id"), nullable=True
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False
    )
    warehouse_slot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouse_slots.id"), nullable=True
    )
    booking_date: Mapped[date] = mapped_column(Date, nullable=False)
    delivery_address: Mapped[str] = mapped_column(Text, nullable=False)
    grain_type: Mapped[str] = mapped_column(String(80), nullable=False)
    quantity_kg: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    status: Mapped[BookingStatus] = mapped_column(
        SAEnum(BookingStatus, name="booking_status", native_enum=False, values_callable=lambda e: [i.value for i in e]),
        default=BookingStatus.PENDING,
        nullable=False,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    warehouse_slot: Mapped["WarehouseSlot | None"] = relationship(lazy="raise", back_populates="bookings")
