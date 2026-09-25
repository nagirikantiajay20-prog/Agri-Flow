from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.farmer._mappers import profile_out
from app.core.database import get_db
from app.core.dependencies import CurrentUser
from app.core.exceptions import ForbiddenError
from app.models.enums import UserRole, UserStatus
from app.models.user import FarmerProfile
from app.schemas import farmer_app as s
from app.schemas.common import MessageResponse, SuccessResponse
from app.services import auth_service, push_service

router = APIRouter(prefix="/auth", tags=["farmer · auth"])


@router.post("/login", response_model=SuccessResponse[s.FarmerLoginResponse])
async def login(body: s.FarmerLoginRequest, db: Annotated[AsyncSession, Depends(get_db)]):
    user = await auth_service.authenticate(db, phone=body.phone, password=body.password)
    if user.role != UserRole.FARMER:
        raise ForbiddenError("This app is for farmers — staff accounts sign in to the admin portal")
    if user.status == UserStatus.PENDING:
        raise ForbiddenError("Your registration is awaiting approval")

    access, refresh, expires_in = await auth_service.issue_token_pair(db, user=user)
    profile = (
        await db.execute(select(FarmerProfile).where(FarmerProfile.user_id == user.id))
    ).scalar_one_or_none()
    return SuccessResponse(
        data=s.FarmerLoginResponse(
            access_token=access,
            refresh_token=refresh,
            expires_in=expires_in,
            user=s.FarmerUser.model_validate(user, from_attributes=True),
            farmer_profile=profile_out(user, profile),
        ),
        message="Signed in",
    )


@router.post("/refresh", response_model=SuccessResponse[s.FarmerTokenResponse])
async def refresh(body: s.FarmerRefreshRequest, db: Annotated[AsyncSession, Depends(get_db)]):
    access, refresh_token, expires_in, user = await auth_service.rotate_refresh_token(
        db, refresh_token=body.refresh_token
    )
    if user.role != UserRole.FARMER:
        raise ForbiddenError("This app is for farmers")
    if user.status != UserStatus.ACTIVE:
        raise ForbiddenError("Account is not active")
    return SuccessResponse(
        data=s.FarmerTokenResponse(access_token=access, refresh_token=refresh_token, expires_in=expires_in)
    )


@router.post("/logout", response_model=MessageResponse)
async def logout(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    body: s.FarmerLogoutRequest | None = None,
):
    if body and body.fcm_token:
        await push_service.unregister_token(db, user_id=user.id, fcm_token=body.fcm_token)
    await auth_service.revoke_all_sessions(db, user_id=user.id)
    return MessageResponse(message="Logged out")
