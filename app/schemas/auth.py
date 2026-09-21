import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models.enums import UserRole, UserStatus
from app.schemas.common import ORMBase


class RegisterRequest(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    phone: str = Field(min_length=8, max_length=20)
    email: EmailStr | None = None
    password: str = Field(min_length=8, max_length=128)
    otp: str | None = None
    role: UserRole = UserRole.FARMER

    @field_validator("role")
    @classmethod
    def farmers_only_self_register(cls, v: UserRole) -> UserRole:
        # Managers/super_admins are created via POST /admin/managers by an
        # existing super_admin (mirrors the verified, correct pattern in
        # agriflow-web/supabase/functions/admin-create-user — Master Plan
        # Module 1/11), never through public self-registration.
        if v != UserRole.FARMER:
            raise ValueError("Only farmer accounts can self-register")
        return v


class OtpSendRequest(BaseModel):
    phone: str = Field(min_length=8, max_length=20)
    purpose: str = Field(default="registration", pattern="^(registration|password_reset)$")


class OtpVerifyRequest(OtpSendRequest):
    code: str = Field(min_length=4, max_length=10)


class OtpIssueResponse(BaseModel):
    expires_in: int
    code: str | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class ForgotPasswordStartRequest(BaseModel):
    phone: str = Field(min_length=8, max_length=20)


class ForgotPasswordResetRequest(BaseModel):
    phone: str = Field(min_length=8, max_length=20)
    code: str = Field(min_length=4, max_length=10)
    new_password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    phone: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class UserPublic(ORMBase):
    id: uuid.UUID
    name: str
    phone: str
    email: str | None
    role: UserRole
    status: UserStatus
    first_login: bool
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserPublic


class UpdateProfileRequest(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    email: EmailStr | None = None
