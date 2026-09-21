from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import ActiveUser, CurrentUser
from app.schemas.auth import (
    ChangePasswordRequest,
    ForgotPasswordResetRequest,
    ForgotPasswordStartRequest,
    LoginRequest,
    OtpIssueResponse,
    OtpSendRequest,
    OtpVerifyRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UpdateProfileRequest,
    UserPublic,
)
from app.schemas.common import MessageResponse, SuccessResponse
from app.services import auth_service, otp_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=SuccessResponse[UserPublic], status_code=201)
async def register(body: RegisterRequest, db: Annotated[AsyncSession, Depends(get_db)]):
    if settings.REQUIRE_REGISTRATION_OTP or body.otp is not None:
        await otp_service.require_valid(
            phone=body.phone, code=body.otp or "", purpose=otp_service.PURPOSE_REGISTRATION
        )
    user = await auth_service.register_farmer(
        db, name=body.name, phone=body.phone, email=body.email, password=body.password
    )
    return SuccessResponse(data=UserPublic.model_validate(user), message="Registration submitted — awaiting approval")


@router.post("/login", response_model=SuccessResponse[TokenResponse])
async def login(body: LoginRequest, db: Annotated[AsyncSession, Depends(get_db)]):
    user = await auth_service.authenticate(db, phone=body.phone, password=body.password)
    access, refresh, expires_in = await auth_service.issue_token_pair(db, user=user)
    return SuccessResponse(
        data=TokenResponse(
            access_token=access, refresh_token=refresh, expires_in=expires_in, user=UserPublic.model_validate(user)
        )
    )


@router.post("/refresh", response_model=SuccessResponse[TokenResponse])
async def refresh(body: RefreshRequest, db: Annotated[AsyncSession, Depends(get_db)]):
    access, refresh_token, expires_in, user = await auth_service.rotate_refresh_token(
        db, refresh_token=body.refresh_token
    )
    return SuccessResponse(
        data=TokenResponse(
            access_token=access, refresh_token=refresh_token, expires_in=expires_in, user=UserPublic.model_validate(user)
        )
    )


@router.post("/logout", response_model=MessageResponse)
async def logout(user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    await auth_service.revoke_all_sessions(db, user_id=user.id)
    return MessageResponse(message="Logged out")


@router.post("/send-otp", response_model=SuccessResponse[OtpIssueResponse])
async def send_otp(body: OtpSendRequest):
    code = await otp_service.issue(phone=body.phone, purpose=body.purpose)
    return SuccessResponse(
        data=OtpIssueResponse(expires_in=otp_service.OTP_TTL_SECONDS, code=code),
        message="If the number is valid, an OTP has been sent",
    )


@router.post("/verify-otp", response_model=MessageResponse)
async def verify_otp(body: OtpVerifyRequest):
    await otp_service.require_valid(
        phone=body.phone, code=body.code, purpose=body.purpose, consume=False
    )
    return MessageResponse(message="OTP verified")


@router.post("/change-password", response_model=MessageResponse)
async def change_password(
    body: ChangePasswordRequest, user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]
):
    await auth_service.change_password(
        db, user=user, current_password=body.current_password, new_password=body.new_password
    )
    return MessageResponse(message="Password changed — all existing sessions revoked")


@router.post("/forgot-password/send-otp", response_model=SuccessResponse[OtpIssueResponse])
async def forgot_password_send_otp(
    body: ForgotPasswordStartRequest, db: Annotated[AsyncSession, Depends(get_db)]
):
    code = await auth_service.start_password_reset(db, phone=body.phone)
    return SuccessResponse(
        data=OtpIssueResponse(expires_in=otp_service.OTP_TTL_SECONDS, code=code),
        message="If the number is registered, a reset OTP has been sent",
    )


@router.post("/forgot-password/reset", response_model=MessageResponse)
async def forgot_password_reset(
    body: ForgotPasswordResetRequest, db: Annotated[AsyncSession, Depends(get_db)]
):
    await auth_service.complete_password_reset(
        db, phone=body.phone, code=body.code, new_password=body.new_password
    )
    return MessageResponse(message="Password reset — you can now sign in")


users_router = APIRouter(prefix="/users", tags=["users"])


@users_router.get("/me", response_model=SuccessResponse[UserPublic])
async def get_me(user: CurrentUser):
    return SuccessResponse(data=UserPublic.model_validate(user))


@users_router.patch("/me", response_model=SuccessResponse[UserPublic])
async def update_me(body: UpdateProfileRequest, user: ActiveUser, db: Annotated[AsyncSession, Depends(get_db)]):
    if body.name:
        user.name = body.name
    if body.email:
        user.email = body.email
    await db.flush()
    return SuccessResponse(data=UserPublic.model_validate(user))
