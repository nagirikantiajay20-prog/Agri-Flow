import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import Pagination, require_permission
from app.schemas.common import MessageResponse, PaginatedResponse, SuccessResponse
from app.schemas.common import Pagination as PaginationSchema
from app.schemas.crop import (
    FarmVisitCreate,
    FarmVisitPublic,
    VisitCompleteRequest,
    VisitScheduleUpdate,
)
from app.services import crop_service

router = APIRouter(prefix="/farm-visits", tags=["farm-visits"])


@router.get("", response_model=PaginatedResponse[FarmVisitPublic])
async def list_farm_visits(
    params: Pagination,
    actor: Annotated[object, Depends(require_permission("visit.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    visits, total = await crop_service.list_visits_for_actor(db, actor=actor, params=params)
    total_pages = (total + params.page_size - 1) // params.page_size if total else 0
    return PaginatedResponse(
        data=[FarmVisitPublic.model_validate(v) for v in visits],
        pagination=PaginationSchema(
            page=params.page, page_size=params.page_size, total=total, total_pages=total_pages
        ),
    )


@router.patch("/{visit_id}/schedule", response_model=SuccessResponse[FarmVisitPublic])
async def schedule_visit(
    visit_id: uuid.UUID,
    body: VisitScheduleUpdate,
    manager: Annotated[object, Depends(require_permission("visit.update"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    visit = await crop_service.schedule_visit(
        db, manager=manager, visit_id=visit_id, staff_id=body.staff_id, scheduled_date=body.scheduled_date
    )
    return SuccessResponse(data=FarmVisitPublic.model_validate(visit))


@router.patch("/{visit_id}/complete", response_model=SuccessResponse[FarmVisitPublic])
async def complete_visit(
    visit_id: uuid.UUID,
    body: VisitCompleteRequest,
    manager: Annotated[object, Depends(require_permission("visit.update"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    visit = await crop_service.complete_visit(
        db, manager=manager, visit_id=visit_id, verified_acres=body.verified_acres, report=body.report
    )
    return SuccessResponse(data=FarmVisitPublic.model_validate(visit))


@router.post("", response_model=SuccessResponse[FarmVisitPublic], status_code=201)
async def create_visit(
    body: FarmVisitCreate,
    manager: Annotated[object, Depends(require_permission("visit.create"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    visit = await crop_service.create_visit(
        db, manager=manager, crop_id=body.crop_id, visit_month=body.visit_month,
        scheduled_date=body.scheduled_date, staff_id=body.staff_id,
    )
    return SuccessResponse(data=FarmVisitPublic.model_validate(visit))


@router.post("/reminders", response_model=MessageResponse)
async def trigger_visit_reminders(
    actor: Annotated[object, Depends(require_permission("visit.update"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    days_ahead: int = 2,
):
    count = await crop_service.send_visit_reminders(db, actor=actor, days_ahead=days_ahead)
    return MessageResponse(message=f"Sent {count} visit reminder(s)")
