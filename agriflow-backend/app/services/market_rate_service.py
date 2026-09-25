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


async def set_rate(
    db: AsyncSession,
    *,
    admin: User,
    crop_type: str,
    grade,
    price_per_kg,
    effective_date: date,
    variety: str | None = None,
) -> MarketRate:
    rate = MarketRate(
        crop_type=crop_type, grade=grade, variety=variety, price_per_kg=price_per_kg,
        effective_date=effective_date, set_by=admin.id,
    )
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


async def current_rates_with_change(db: AsyncSession) -> list[dict]:
    """Latest rate per (crop_type, grade) plus its change against the
    previous effective rate, computed from history in one windowed query
    rather than stored, so it can never drift from the real price series."""
    from sqlalchemy import func

    ranked = (
        select(
            MarketRate,
            func.row_number()
            .over(
                partition_by=(MarketRate.crop_type, MarketRate.grade),
                order_by=(MarketRate.effective_date.desc(), MarketRate.created_at.desc()),
            )
            .label("rn"),
        )
        .where(MarketRate.effective_date <= date.today())
        .subquery()
    )
    rows = (
        await db.execute(
            select(ranked).where(ranked.c.rn <= 2).order_by(ranked.c.crop_type, ranked.c.grade, ranked.c.rn)
        )
    ).mappings().all()

    out: list[dict] = []
    for row in rows:
        if row["rn"] == 1:
            out.append(
                {
                    "id": row["id"],
                    "crop_type": row["crop_type"],
                    "grade": row["grade"],
                    "variety": row["variety"],
                    "price_per_kg": Decimal(row["price_per_kg"]),
                    "effective_date": row["effective_date"],
                    "change_percentage": Decimal("0"),
                }
            )
        elif out and out[-1]["crop_type"] == row["crop_type"] and out[-1]["grade"] == row["grade"]:
            previous = Decimal(row["price_per_kg"])
            if previous:
                out[-1]["change_percentage"] = (
                    (out[-1]["price_per_kg"] - previous) / previous * 100
                ).quantize(Decimal("0.01"))
    return out
