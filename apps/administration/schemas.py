from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, ConfigDict, field_validator

from common.enums import Role,AdminUserStatus


from common.schemas import ApiResponse


class AdminUserCreateRequest(BaseModel):
    firstName: str
    lastName: str
    email: EmailStr
    role: Literal["user", "moderator", "viewer"]


class CamelModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)



class AdminUserActionRequest(BaseModel):
    id: str

class AdminDeleteUsersRequest(BaseModel):
    userIds: list[str]


class AdminUserStatusRequest(BaseModel):
    status: AdminUserStatus

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_at: datetime | None = None




class AdminLoginRequest(BaseModel):
    email: EmailStr
    password: str

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        if not value or not value.strip():
            raise ValueError("email cannot be blank")
        return value.lower().strip()


class AdminOnboardingRequest(BaseModel):
    profile_photo_key: str = Field(..., description="S3 storage key returned by POST /uploads/image")
    university_id: str
    major: str
    minor: Optional[str] = None
    education_level_id: int
    bio: str = Field(..., max_length=500)
    academic_interests: list[str | int]


class AdminForgotPasswordRequest(BaseModel):
    email: EmailStr


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)

    @field_validator("new_password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if not any(char.isupper() for char in value):
            raise ValueError("password must contain at least one uppercase letter")
        if not any(char.isdigit() for char in value):
            raise ValueError("password must contain at least one number")
        return value



class AdminEditProfileRequest(BaseModel):
    profile_photo_key: str | None = None
    banner_photo_key: str | None = None
    firstName: str  | None = None
    lastName: str | None = None



class AdminResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8)

    @field_validator("new_password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if not any(char.isupper() for char in value):
            raise ValueError("password must contain at least one uppercase letter")
        if not any(char.isdigit() for char in value):
            raise ValueError("password must contain at least one number")
        return value


class AdminPublishPostRequest(BaseModel):
    post_id: UUID
    status: Literal["publish", "flag"] = "publish"

