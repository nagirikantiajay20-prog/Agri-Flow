"""
Crops & Farm Visits (Master Plan Module 5).

Note: `crop_inspections` deliberately does NOT live in this file — it is
tied to grain procurement quality checks, not farm-visit crop health
checks, despite the name (Master Plan §1.6 correction). See
app/models/grain.py.
"""
import uuid
from datetime import date

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import CropStatus, VisitStatus
from app.models.mixins import CreatedAtOnlyMixin, TimestampMixin, UUIDPKMixin

# Auto-scheduled visit months per crop type — ported verbatim from the
# verified logic in agriflow-web/supabase/functions/farmer-api/index.ts
# `registerCrop` (Master Plan Module 5). Centralized here as the single
# source of truth instead of being duplicated per call site.
VISIT_MONTHS_BY_CROP: dict[str, list[int]] = {
    "Rice": [1, 3],
    "Wheat": [1, 3],
    "Maize": [1, 2],
    "Cotton": [1, 4],
    "Groundnut": [1, 3],
    "Sugarcane": [2, 5],
    "Turmeric": [2, 6],
    "Chili": [1, 3],
}
DEFAULT_VISIT_MONTHS = [1, 3]


class Crop(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "crops"
    __table_args__ = (
        Index("ix_crops_farmer_status", "farmer_id", "status"),
        Index("ix_crops_status_created", "status", "created_at"),
    )

    farmer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    crop_type: Mapped[str] = mapped_column(String(80), nullable=False)
    acres: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    sowing_date: Mapped[date] = mapped_column(Date, nullable=False)
    harvest_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[CropStatus] = mapped_column(
        SAEnum(CropStatus, name="crop_status", native_enum=False, values_callable=lambda e: [i.value for i in e]),
        default=CropStatus.GROWING,
        nullable=False,
    )
    current_month: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    farm_visits: Mapped[list["FarmVisit"]] = relationship(
        lazy="raise", back_populates="crop", cascade="all, delete-orphan", passive_deletes=True
    )


class FarmVisit(Base, UUIDPKMixin, CreatedAtOnlyMixin):
    __tablename__ = "farm_visits"
    __table_args__ = (
        CheckConstraint("visit_month IN (1,2,3,4,5,6)", name="ck_farm_visits_visit_month"),
        Index("ix_farm_visits_farmer_status", "farmer_id", "status"),
        Index("ix_farm_visits_scheduled_status", "scheduled_date", "status"),
        Index("ix_farm_visits_crop_month", "crop_id", "visit_month"),
    )

    crop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crops.id", ondelete="CASCADE"), nullable=False, index=True
    )
    farmer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    staff_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    visit_month: Mapped[int] = mapped_column(Integer, nullable=False)
    scheduled_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    actual_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[VisitStatus] = mapped_column(
        SAEnum(VisitStatus, name="visit_status", native_enum=False, values_callable=lambda e: [i.value for i in e]),
        default=VisitStatus.SCHEDULED,
        nullable=False,
    )
    verified_acres: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    report: Mapped[str | None] = mapped_column(Text, nullable=True)

    crop: Mapped["Crop"] = relationship(lazy="raise", back_populates="farm_visits")
