from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import cache
from app.core.config import settings
from app.core.database import get_db
from app.schemas.admin import MarketRatePublic, PublicStatsResponse
from app.schemas.common import SuccessResponse
from app.schemas.seed import SeedPublic
from app.services import ledger_service, market_rate_service, purchase_service

router = APIRouter(prefix="/public", tags=["public"])


@router.get("/market-rates", response_model=SuccessResponse[list[MarketRatePublic]])
async def market_rates(db: Annotated[AsyncSession, Depends(get_db)]):
    rates = await market_rate_service.list_current_rates(db)
    return SuccessResponse(data=[MarketRatePublic.model_validate(r) for r in rates])


@router.get("/seeds", response_model=SuccessResponse[list[SeedPublic]])
async def seed_catalog(db: Annotated[AsyncSession, Depends(get_db)]):
    seeds = await purchase_service.list_seeds(db, active_only=True)
    return SuccessResponse(data=[SeedPublic.model_validate(s) for s in seeds])


@router.get("/stats", response_model=SuccessResponse[PublicStatsResponse])
async def platform_stats(db: Annotated[AsyncSession, Depends(get_db)]):
    stats = await cache.get_or_set(
        cache.key(cache.PUBLIC_NAMESPACE, "stats"),
        settings.CACHE_TTL_PUBLIC_SECONDS,
        lambda: ledger_service.get_public_stats(db),
    )
    return SuccessResponse(data=PublicStatsResponse(**stats))
