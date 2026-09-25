import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import cache
from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import Pagination, require_permission
from app.schemas.admin import (
    AdminDashboardResponse,
    AuditLogPublic,
    ManagerCreate,
    ManagerPublic,
    ManagerStatusUpdate,
    ManagerUpdate,
    MarketRateCreate,
    MarketRatePublic,
    MonthlyReportResponse,
    ResetPasswordRequest,
)
from app.schemas.common import MessageResponse, PaginatedResponse, SuccessResponse
from app.schemas.common import Pagination as PaginationSchema
from app.services import admin_service, market_rate_service

router = APIRouter(prefix="/admin", tags=["admin"])
managers_router = APIRouter(prefix="/managers", tags=["managers"])
market_rates_router = APIRouter(prefix="/market-rates", tags=["market-rates"])


@managers_router.post("", response_model=SuccessResponse[ManagerPublic], status_code=201)
async def create_manager(
    body: ManagerCreate,
    creator: Annotated[object, Depends(require_permission("manager.manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    manager = await admin_service.create_manager(
        db, creator=creator, name=body.name, phone=body.phone, email=body.email, password=body.password,
        role=body.role, assigned_region=body.assigned_region, department=body.department,
    )
    return SuccessResponse(data=ManagerPublic.model_validate(manager))


@managers_router.get("", response_model=PaginatedResponse[ManagerPublic])
async def list_managers(
    params: Pagination,
    _: Annotated[object, Depends(require_permission("manager.manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    managers, total = await admin_service.list_managers(db, params=params)
    total_pages = (total + params.page_size - 1) // params.page_size if total else 0
    return PaginatedResponse(
        data=[ManagerPublic.model_validate(m) for m in managers],
        pagination=PaginationSchema(page=params.page, page_size=params.page_size, total=total, total_pages=total_pages),
    )


@managers_router.patch("/{manager_id}/status", response_model=SuccessResponse[ManagerPublic])
async def update_manager_status(
    manager_id: uuid.UUID,
    body: ManagerStatusUpdate,
    actor: Annotated[object, Depends(require_permission("manager.manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    manager = await admin_service.update_manager_status(db, actor=actor, manager_id=manager_id, new_status=body.status)
    return SuccessResponse(data=ManagerPublic.model_validate(manager))


@managers_router.patch("/{manager_id}", response_model=SuccessResponse[ManagerPublic])
async def update_manager(
    manager_id: uuid.UUID,
    body: ManagerUpdate,
    actor: Annotated[object, Depends(require_permission("manager.manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    manager = await admin_service.update_manager(
        db, actor=actor, manager_id=manager_id, **body.model_dump(exclude_unset=True)
    )
    return SuccessResponse(data=ManagerPublic.model_validate(manager))


@managers_router.post("/{manager_id}/reset-password", response_model=MessageResponse)
async def reset_manager_password(
    manager_id: uuid.UUID,
    body: ResetPasswordRequest,
    # super_admin ONLY — mirrors the one verified-correct check in the
    # legacy admin-create-user Edge Function (Master Plan §1.1/Module 11).
    actor: Annotated[object, Depends(require_permission("manager.manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await admin_service.reset_manager_password(db, actor=actor, manager_id=manager_id, new_password=body.new_password)
    return MessageResponse(message="Password reset — all existing sessions revoked")


@router.get("/dashboard", response_model=SuccessResponse[AdminDashboardResponse])
async def get_dashboard(
    _: Annotated[object, Depends(require_permission("dashboard.admin.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    data = await cache.get_or_set(
        cache.key(cache.DASHBOARD_NAMESPACE, "admin"),
        settings.CACHE_TTL_DASHBOARD_SECONDS,
        lambda: admin_service.get_dashboard(db),
    )
    return SuccessResponse(data=AdminDashboardResponse(**data))


@router.get("/reports/monthly", response_model=SuccessResponse[MonthlyReportResponse])
async def get_monthly_report(
    _: Annotated[object, Depends(require_permission("report.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$", description="YYYY-MM"),
):
    data = await admin_service.get_monthly_report(db, month=month)
    return SuccessResponse(data=MonthlyReportResponse(**data))


@router.get("/audit-logs", response_model=PaginatedResponse[AuditLogPublic])
async def get_audit_logs(
    params: Pagination,
    _: Annotated[object, Depends(require_permission("audit.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    logs, total = await admin_service.list_audit_logs(db, params=params)
    total_pages = (total + params.page_size - 1) // params.page_size if total else 0
    return PaginatedResponse(
        data=[AuditLogPublic.model_validate(log) for log in logs],
        pagination=PaginationSchema(page=params.page, page_size=params.page_size, total=total, total_pages=total_pages),
    )


@market_rates_router.post("", response_model=SuccessResponse[MarketRatePublic], status_code=201)
async def set_market_rate(
    body: MarketRateCreate,
    admin: Annotated[object, Depends(require_permission("market_rate.manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    rate = await market_rate_service.set_rate(
        db, admin=admin, crop_type=body.crop_type, grade=body.grade,
        price_per_kg=body.price_per_kg, effective_date=body.effective_date, variety=body.variety,
    )
    return SuccessResponse(data=MarketRatePublic.model_validate(rate))


@market_rates_router.get("", response_model=SuccessResponse[list[MarketRatePublic]])
async def list_market_rates(
    _: Annotated[object, Depends(require_permission("market_rate.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    rates = await market_rate_service.list_current_rates(db)
    return SuccessResponse(data=[MarketRatePublic.model_validate(r) for r in rates])
