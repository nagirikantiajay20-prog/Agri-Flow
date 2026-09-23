"""
Read-side aggregation for the farmer mobile app.

Writes go through the shared services (crop_service, purchase_service,
booking_service, ...) exactly as they do for the web app, so both clients
get the same locking, validation and audit trail. This module only
assembles the screens the phone needs in as few round trips as possible.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.crop import Crop, FarmVisit
from app.models.enums import (
    BookingStatus,
    CropStatus,
    PaymentStatus,
    TransactionDirection,
    TransactionStatus,
    VisitStatus,
)
from app.models.ledger import Notification, Transaction
from app.models.seed import Seed, SeedPurchase
from app.models.user import FarmerProfile, User
from app.models.warehouse import BookingSlot, Warehouse
from app.services import market_rate_service, weather_service

logger = get_logger(__name__)

TYPICAL_CROP_DAYS: dict[str, int] = {
    "rice": 120, "paddy": 120, "wheat": 120, "maize": 100, "cotton": 160,
    "groundnut": 110, "sugarcane": 330, "turmeric": 240, "chili": 150, "pulses": 90,
}
DEFAULT_CROP_DAYS = 120
EARNINGS_WINDOW_DAYS = 30
RECENT_ORDERS = 5


def stage_progress_percent(crop: Crop, today: date | None = None) -> int:
    today = today or date.today()
    if crop.harvest_date and crop.harvest_date > crop.sowing_date:
        span = (crop.harvest_date - crop.sowing_date).days
    else:
        span = TYPICAL_CROP_DAYS.get(crop.crop_type.lower(), DEFAULT_CROP_DAYS)
    elapsed = (today - crop.sowing_date).days
    return max(0, min(100, round(elapsed * 100 / span)))


def payment_status_label(status: PaymentStatus, payment_method: str | None) -> str:
    if status == PaymentStatus.PAID:
        return "Paid"
    if status == PaymentStatus.FAILED:
        return "Failed"
    if (payment_method or "warehouse") == "warehouse":
        return "Unpaid (Pay at Warehouse)"
    return "Payment pending"


async def _health_by_crop(db: AsyncSession, crop_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Latest officer diagnosis per crop; honest placeholders otherwise —
    the app must never show a health verdict nobody actually made."""
    if not crop_ids:
        return {}
    rows = (
        await db.execute(
            select(FarmVisit.crop_id, FarmVisit.status, FarmVisit.diagnosis)
            .where(FarmVisit.crop_id.in_(crop_ids))
            .where(FarmVisit.status.in_([VisitStatus.COMPLETED, VisitStatus.PENDING_REVIEW]))
            .order_by(FarmVisit.crop_id, FarmVisit.actual_date.desc().nullslast(), FarmVisit.created_at.desc())
        )
    ).all()
    health: dict[uuid.UUID, str] = {}
    for crop_id, status, diagnosis in rows:
        if crop_id in health:
            continue
        if status == VisitStatus.COMPLETED and diagnosis:
            health[crop_id] = diagnosis
        elif status == VisitStatus.PENDING_REVIEW:
            health[crop_id] = "Awaiting officer review"
        else:
            health[crop_id] = "Inspected"
    return health


async def recent_orders(db: AsyncSession, *, farmer: User, limit: int = RECENT_ORDERS) -> list[dict]:
    purchases = (
        await db.execute(
            select(SeedPurchase, Seed.name)
            .join(Seed, Seed.id == SeedPurchase.seed_id)
            .where(SeedPurchase.farmer_id == farmer.id)
            .order_by(SeedPurchase.created_at.desc())
            .limit(limit)
        )
    ).all()
    bookings = (
        await db.execute(
            select(BookingSlot, Warehouse.name)
            .join(Warehouse, Warehouse.id == BookingSlot.warehouse_id)
            .where(BookingSlot.farmer_id == farmer.id)
            .order_by(BookingSlot.created_at.desc())
            .limit(limit)
        )
    ).all()

    orders = [
        (
            p.created_at,
            {
                "id": p.id,
                "reference": p.invoice_number or f"ORD-{str(p.id)[:8].upper()}",
                "type": "seed_purchase",
                "title": seed_name,
                "date": p.created_at.date(),
                "status": payment_status_label(p.payment_status, p.payment_method),
                "amount": Decimal(p.total_amount),
            },
        )
        for p, seed_name in purchases
    ] + [
        (
            b.created_at,
            {
                "id": b.id,
                "reference": f"GBK-{str(b.id)[:8].upper()}",
                "type": "grain_booking",
                "title": f"Grain Booking - {warehouse_name}",
                "date": b.booking_date,
                "status": b.status.value.capitalize(),
                "amount": Decimal("0"),
            },
        )
        for b, warehouse_name in bookings
    ]
    orders.sort(key=lambda pair: pair[0], reverse=True)
    return [o for _, o in orders[:limit]]


async def dashboard(db: AsyncSession, *, farmer: User) -> dict:
    weather_task = asyncio.create_task(weather_service.current_weather())

    profile = (
        await db.execute(select(FarmerProfile).where(FarmerProfile.user_id == farmer.id))
    ).scalar_one_or_none()

    since = datetime.now(timezone.utc) - timedelta(days=EARNINGS_WINDOW_DAYS)
    totals = (
        await db.execute(
            select(
                select(func.count())
                .select_from(BookingSlot)
                .where(
                    BookingSlot.farmer_id == farmer.id,
                    BookingSlot.status.in_([BookingStatus.PENDING, BookingStatus.CONFIRMED]),
                )
                .scalar_subquery()
                .label("pending_bookings"),
                select(func.coalesce(func.sum(Transaction.amount), 0))
                .where(
                    Transaction.farmer_id == farmer.id,
                    Transaction.direction == TransactionDirection.CREDIT,
                    Transaction.status == TransactionStatus.COMPLETED,
                    Transaction.created_at >= since,
                )
                .scalar_subquery()
                .label("recent_earnings"),
                select(func.count())
                .select_from(Notification)
                .where(Notification.user_id == farmer.id, Notification.is_read.is_(False))
                .scalar_subquery()
                .label("unread"),
            )
        )
    ).one()

    crops = list(
        (
            await db.execute(
                select(Crop)
                .where(
                    Crop.farmer_id == farmer.id,
                    Crop.status == CropStatus.GROWING,
                    Crop.deleted_at.is_(None),
                )
                .order_by(Crop.created_at.desc())
            )
        ).scalars().all()
    )
    health = await _health_by_crop(db, [c.id for c in crops])
    rates = await market_rate_service.current_rates_with_change(db)
    orders = await recent_orders(db, farmer=farmer)

    try:
        weather = await asyncio.wait_for(weather_task, timeout=8.0)
    except Exception as exc:
        logger.warning(
            "dashboard_weather_task_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        weather = None

    return {
        "farmer_id": farmer.id,
        "farmer_name": farmer.name,
        "farm_name": profile.farm_name if profile else None,
        "active_crops_count": len(crops),
        "total_acres": sum((Decimal(c.acres) for c in crops), Decimal("0")),
        "pending_bookings": totals.pending_bookings,
        "recent_earnings": Decimal(totals.recent_earnings),
        "unread_notifications": totals.unread,
        "weather": weather,
        "crops": [
            {
                "id": c.id,
                "crop_name": c.crop_name,
                "crop_type": c.crop_type,
                "acres": Decimal(c.acres),
                "stage": c.stage,
                "stage_progress_percent": stage_progress_percent(c),
                "health_status": health.get(c.id, "Not inspected yet"),
            }
            for c in crops
        ],
        "mandi_prices": rates,
        "recent_orders": orders,
    }
