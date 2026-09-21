"""
Grain Sales & Procurement Quality Inspection (Master Plan Module 8).

`CropInspection` lives here — NOT in app/models/crop.py — because it is
inserted at grain-procurement/quality-check time (keyed by
booking_slot_id + grain_sale_id), not during farm-visit crop health
checks. The name is inherited from the legacy `inspect_crop` RPC and is
misleading; this was a real miscategorization in the first pass of the
audit, corrected in Master Plan §1.6.
"""
import uuid

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import GrainGrade, GrainSaleStatus
from app.models.mixins import CreatedAtOnlyMixin, TimestampMixin, UUIDPKMixin


class GrainSale(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "grain_sales"
    __table_args__ = (
        Index("ix_grain_sales_farmer_status", "farmer_id", "status"),
        Index("ix_grain_sales_status_updated", "status", "updated_at"),
    )

    farmer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    crop_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("crops.id"), nullable=True)
    grain_type: Mapped[str] = mapped_column(String(80), nullable=False)
    grade: Mapped[GrainGrade] = mapped_column(
        SAEnum(GrainGrade, name="grain_grade", native_enum=False, values_callable=lambda e: [i.value for i in e]),
        nullable=False,
    )
    raw_material_kg: Mapped[float] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    wastage_kg: Mapped[float] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    good_material_kg: Mapped[float] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    price_per_kg: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    total_amount: Mapped[float] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    status: Mapped[GrainSaleStatus] = mapped_column(
        SAEnum(GrainSaleStatus, name="grain_sale_status", native_enum=False, values_callable=lambda e: [i.value for i in e]),
        default=GrainSaleStatus.PENDING,
        nullable=False,
    )

    inspections: Mapped[list["CropInspection"]] = relationship(lazy="raise", back_populates="grain_sale")


class CropInspection(Base, UUIDPKMixin, CreatedAtOnlyMixin):
    __tablename__ = "crop_inspections"

    booking_slot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("booking_slots.id"), nullable=False, index=True
    )
    grain_sale_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("grain_sales.id"), nullable=False, index=True
    )
    inspector_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    good_quantity_kg: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    bad_quantity_kg: Mapped[float] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    grain_sale: Mapped["GrainSale"] = relationship(lazy="raise", back_populates="inspections")
