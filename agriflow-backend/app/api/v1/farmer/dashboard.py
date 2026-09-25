from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.farmer._mappers import rate_out
from app.core.database import get_db
from app.core.dependencies import require_farmer
from app.models.user import User
from app.schemas import farmer_app as s
from app.schemas.common import SuccessResponse
from app.services import farmer_app_service, market_rate_service

router = APIRouter(tags=["farmer · dashboard"])


@router.get("/dashboard", response_model=SuccessResponse[s.DashboardOut])
async def get_dashboard(
    farmer: Annotated[User, Depends(require_farmer("dashboard.farmer.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    data = await farmer_app_service.dashboard(db, farmer=farmer)
    data["mandi_prices"] = [rate_out(r) for r in data["mandi_prices"]]
    return SuccessResponse(data=s.DashboardOut(**data))


@router.get("/market-rates", response_model=SuccessResponse[list[s.MarketRateOut]])
async def market_rates(
    _: Annotated[User, Depends(require_farmer("market_rate.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    rates = await market_rate_service.current_rates_with_change(db)
    return SuccessResponse(data=[rate_out(r) for r in rates])
