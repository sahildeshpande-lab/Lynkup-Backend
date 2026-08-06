from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, ConfigDict, field_validator, model_validator

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
    role: Literal["user", "moderator", "viewer"]


class AdminUserStatusRequest(BaseModel):
    status: AdminUserStatus
    note: str | None = Field(
        default=None,
        max_length=5000,
        description="Reason for the status change (required when suspending or banning).",
    )

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @model_validator(mode="after")
    def require_note_for_restrictive_status(self) -> "AdminUserStatusRequest":
        if self.status in (AdminUserStatus.suspended, AdminUserStatus.banned) and not self.note:
            raise ValueError("note is required when suspending or banning a user")
        return self

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_at: datetime | None = None


class AdminSignupRequest(BaseModel):
    firstName: str
    lastName: str
    email: EmailStr
    password: str = Field(min_length=8, max_length=20)
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
    status: Literal[
        "published", "flagged", "rejected", "reinstate", "escalate"
    ] = "published"
    notes: str | None = Field(
        default=None,
        max_length=5000,
        description="Optional moderation notes.",
    )


class RecommendationSettingsResponse(BaseModel):
    is_enabled: bool
    generation_frequency_days: int
    max_recommendations: int
    # GET returns admin full name; PATCH still returns UUID.
    updated_by: UUID | str | None
    updated_at: datetime | None
    created_at: datetime | None = None


class RecommendationSettingsChangeItem(BaseModel):
    field: str
    previous_value: Any
    new_value: Any


class RecommendationSettingsHistoryItem(BaseModel):
    id: UUID
    updated_by: str | None = None
    updated_at: datetime | None
    changes: list[RecommendationSettingsChangeItem]


class RecommendationSettingsWithHistoryResponse(BaseModel):
    current_settings: RecommendationSettingsResponse
    history: list[RecommendationSettingsHistoryItem]


class RecommendationSettingsUpdateRequest(BaseModel):
    # All fields optional; only provided fields are updated.
    is_enabled: bool | None = None
    generation_frequency_days: int | None = Field(default=None, ge=1, le=365)
    max_recommendations: int | None = Field(default=None, ge=1, le=50)

