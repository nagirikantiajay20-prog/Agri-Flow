from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ledger import MarketRate
from app.models.user import User
from app.services import audit_service


async def list_current_rates(db: AsyncSession) -> list[MarketRate]:
    # Latest rate per (crop_type, grade) as of today.
    result = await db.execute(
        select(MarketRate).where(MarketRate.effective_date <= date.today()).order_by(MarketRate.effective_date.desc())
    )
    rows = result.scalars().all()
    latest: dict[tuple[str, str], MarketRate] = {}
    for row in rows:
        key = (row.crop_type, row.grade.value)
        if key not in latest:
            latest[key] = row
    return list(latest.values())


async def set_rate(db: AsyncSession, *, admin: User, crop_type: str, grade, price_per_kg, effective_date: date) -> MarketRate:
    rate = MarketRate(crop_type=crop_type, grade=grade, price_per_kg=price_per_kg, effective_date=effective_date, set_by=admin.id)
    db.add(rate)
    await db.flush()
    await audit_service.record(
        db, actor_id=admin.id, action="market_rate.set", entity_type="market_rate", entity_id=rate.id,
        new_value={"crop_type": crop_type, "price_per_kg": str(price_per_kg)},
    )
    return rate


async def resolve_price_per_kg(db: AsyncSession, *, crop_type: str, grade) -> Decimal:
    result = await db.execute(
        select(MarketRate.price_per_kg)
        .where(
            MarketRate.crop_type == crop_type,
            MarketRate.grade == grade,
            MarketRate.effective_date <= date.today(),
        )
        .order_by(MarketRate.effective_date.desc(), MarketRate.created_at.desc())
        .limit(1)
    )
    price = result.scalar_one_or_none()
    return Decimal(price) if price is not None else Decimal("0")
