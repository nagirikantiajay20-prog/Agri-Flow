from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.farmer._mappers import profile_out
from app.core.database import get_db
from app.core.dependencies import require_farmer
from app.core.exceptions import ConflictError, NotFoundError
from app.models.user import FarmerProfile, User
from app.schemas import farmer_app as s
from app.schemas.common import SuccessResponse
from app.services import audit_service, farmer_service

router = APIRouter(prefix="/profile", tags=["farmer · profile"])

USER_FIELDS = {"name", "email"}


async def _load(db: AsyncSession, farmer: User) -> FarmerProfile:
    profile = (
        await db.execute(select(FarmerProfile).where(FarmerProfile.user_id == farmer.id))
    ).scalar_one_or_none()
    if profile is None:
        raise NotFoundError("Farmer profile not found")
    return profile


@router.get("", response_model=SuccessResponse[s.FarmerProfileOut])
async def get_profile(
    farmer: Annotated[User, Depends(require_farmer("profile.read.own"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return SuccessResponse(data=profile_out(farmer, await _load(db, farmer)))


@router.patch("", response_model=SuccessResponse[s.FarmerProfileOut])
@router.put("", response_model=SuccessResponse[s.FarmerProfileOut], include_in_schema=False)
async def update_profile(
    body: s.FarmerProfileUpdate,
    farmer: Annotated[User, Depends(require_farmer("profile.update.own"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    updates = body.model_dump(exclude_unset=True)
    profile = await _load(db, farmer)

    if updates.get("email") and updates["email"] != farmer.email:
        taken = await db.execute(select(User.id).where(User.email == updates["email"], User.id != farmer.id))
        if taken.scalar_one_or_none() is not None:
            raise ConflictError("That email address is already used by another account")

    for field, value in updates.items():
        setattr(farmer if field in USER_FIELDS else profile, field, value)
    await db.flush()
    await audit_service.record(
        db, actor_id=farmer.id, action="profile.update", entity_type="user", entity_id=farmer.id,
        new_value={k: str(v) for k, v in updates.items()},
    )
    return SuccessResponse(data=profile_out(farmer, profile), message="Profile updated")


@router.post("/bank-request", response_model=SuccessResponse[s.BankChangeOut], status_code=201)
async def request_bank_change(
    body: s.BankChangeIn,
    farmer: Annotated[User, Depends(require_farmer("bank_change.request"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    req = await farmer_service.request_bank_change(
        db, farmer=farmer, bank_name=body.bank_name, account_number=body.account_number,
        ifsc_code=body.ifsc_code, upi_id=body.upi_id,
    )
    return SuccessResponse(
        data=s.BankChangeOut(request_id=req.id, status=req.status.value),
        message="Bank change request submitted for review",
    )
