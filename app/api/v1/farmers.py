import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import Pagination, require_permission
from app.schemas.common import PaginatedResponse, SuccessResponse
from app.schemas.common import Pagination as PaginationSchema
from app.schemas.farmer import (
    BankChangeRequestCreate,
    BankChangeRequestPublic,
    BankChangeReviewRequest,
    FarmerAdminView,
    FarmerApprovalRequest,
    FarmerCreateRequest,
    FarmerDashboardResponse,
    FarmerProfilePublic,
    UpdateFarmerProfileRequest,
)
from app.services import farmer_service

router = APIRouter(prefix="/farmers", tags=["farmers"])


@router.get("/me", response_model=SuccessResponse[FarmerProfilePublic])
async def get_my_profile(
    user: Annotated[object, Depends(require_permission("profile.read.own"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    _, profile = await farmer_service.get_own_profile(db, user=user)
    return SuccessResponse(data=FarmerProfilePublic.model_validate(profile))


@router.patch("/me", response_model=SuccessResponse[FarmerProfilePublic])
async def update_my_profile(
    body: UpdateFarmerProfileRequest,
    user: Annotated[object, Depends(require_permission("profile.update.own"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    profile = await farmer_service.update_own_profile(db, user=user, updates=body.model_dump(exclude_unset=True))
    return SuccessResponse(data=FarmerProfilePublic.model_validate(profile))


@router.get("/me/dashboard", response_model=SuccessResponse[FarmerDashboardResponse])
async def get_my_dashboard(
    farmer: Annotated[object, Depends(require_permission("dashboard.farmer.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    data = await farmer_service.get_farmer_dashboard(db, farmer=farmer)
    return SuccessResponse(data=FarmerDashboardResponse.model_validate(data))


@router.post("/me/bank-change-requests", response_model=SuccessResponse[BankChangeRequestPublic], status_code=201)
async def request_bank_change(
    body: BankChangeRequestCreate,
    user: Annotated[object, Depends(require_permission("bank_change.request"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    req = await farmer_service.request_bank_change(
        db, farmer=user, bank_name=body.bank_name, account_number=body.account_number,
        ifsc_code=body.ifsc_code, upi_id=body.upi_id,
    )
    return SuccessResponse(data=BankChangeRequestPublic.model_validate(req))


@router.get("/bank-change-requests", response_model=PaginatedResponse[BankChangeRequestPublic])
async def list_bank_change_requests(
    params: Pagination,
    _: Annotated[object, Depends(require_permission("bank_change.review"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    rows, total = await farmer_service.list_bank_change_requests(db, params=params)
    total_pages = (total + params.page_size - 1) // params.page_size if total else 0
    return PaginatedResponse(
        data=[BankChangeRequestPublic.model_validate(r) for r in rows],
        pagination=PaginationSchema(page=params.page, page_size=params.page_size, total=total, total_pages=total_pages),
    )


@router.patch("/bank-change-requests/{request_id}", response_model=SuccessResponse[BankChangeRequestPublic])
async def review_bank_change(
    request_id: uuid.UUID,
    body: BankChangeReviewRequest,
    reviewer: Annotated[object, Depends(require_permission("bank_change.review"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    req = await farmer_service.review_bank_change(
        db, reviewer=reviewer, request_id=request_id, approve=body.approve, admin_notes=body.admin_notes
    )
    return SuccessResponse(data=BankChangeRequestPublic.model_validate(req))


@router.get("", response_model=PaginatedResponse[FarmerAdminView])
async def list_farmers(
    params: Pagination,
    _: Annotated[object, Depends(require_permission("farmer.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    farmers, total = await farmer_service.list_farmers(db, params=params)
    total_pages = (total + params.page_size - 1) // params.page_size if total else 0
    return PaginatedResponse(
        data=[FarmerAdminView.model_validate(f) for f in farmers],
        pagination=PaginationSchema(page=params.page, page_size=params.page_size, total=total, total_pages=total_pages),
    )


@router.patch("/{farmer_id}/approval", response_model=SuccessResponse[FarmerAdminView])
async def set_farmer_approval(
    farmer_id: uuid.UUID,
    body: FarmerApprovalRequest,
    reviewer: Annotated[object, Depends(require_permission("farmer.approve"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    farmer = await farmer_service.set_approval(db, reviewer=reviewer, farmer_id=farmer_id, new_status=body.status)
    return SuccessResponse(data=FarmerAdminView.model_validate(farmer))


@router.post("", response_model=SuccessResponse[FarmerAdminView], status_code=201)
async def create_farmer(
    body: FarmerCreateRequest,
    creator: Annotated[object, Depends(require_permission("farmer.create"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    farmer = await farmer_service.create_farmer(
        db, creator=creator, name=body.name, phone=body.phone, email=body.email,
        password=body.password, address=body.address, acres_of_land=body.acres_of_land,
        crop_address=body.crop_address,
    )
    return SuccessResponse(data=FarmerAdminView.model_validate(farmer))


@router.get("/{farmer_id}", response_model=SuccessResponse[FarmerAdminView])
async def get_farmer(
    farmer_id: uuid.UUID,
    _: Annotated[object, Depends(require_permission("farmer.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    farmer, _profile = await farmer_service.get_farmer_detail(db, farmer_id=farmer_id)
    return SuccessResponse(data=FarmerAdminView.model_validate(farmer))
