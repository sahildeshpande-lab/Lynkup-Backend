from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Literal

from pydantic import BaseModel, EmailStr, Field, ConfigDict, field_validator

from common.enums import Role,AdminUserStatus


class ApiResponse(BaseModel):
    status: bool = True
    message: str = "success"
    data: Any | None = None



class AdminUserCreateRequest(BaseModel):
    firstName: str
    lastName: str
    email: EmailStr
    role: Role


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


class AdminSignupRequest(BaseModel):
    firstName: str
    lastName: str
    email: EmailStr
    password: str = Field(min_length=8 , max_length=20)
    role: Role = "superadmin"

    @field_validator("firstName", "lastName")
    @classmethod
    def validate_names(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("names cannot be blank")
        return value.strip()

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        if not value or not value.strip():
            raise ValueError("email cannot be blank")
        return value.lower().strip()

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("password cannot be blank")
        if not any(char.isupper() for char in value):
            raise ValueError("password must contain at least one uppercase letter")
        if not any(char.isdigit() for char in value):
            raise ValueError("password must contain at least one number")
        return value


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
    profilePhotoKey: str | None = None
    firstName: str
    lastName: str



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

