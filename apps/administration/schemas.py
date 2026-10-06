from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional, Literal
from uuid import UUID

from pydantic import (
    AliasChoices,
    BaseModel,
    EmailStr,
    Field,
    ConfigDict,
    field_validator,
)
from pydantic_core import PydanticCustomError

from common.enums import Role, AdminUserStatus


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
        description="Optional reason when changing a user's status.",
    )

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_at: datetime | None = None


class AdminSignupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    firstName: str
    lastName: str
    email: EmailStr
    password: str = Field(min_length=8, max_length=20)
    role: Role

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
    # Optional pre-login signing-key registration id. Bound only after auth succeeds.
    keyId: UUID | None = None

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        if not value or not value.strip():
            raise ValueError("email cannot be blank")
        return value.lower().strip()


class AdminSigningKeyRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    publicKey: str = Field(..., min_length=1, description="RSA public key PEM or base64 SPKI")


class AdminSigningKeyRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keyId: UUID


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
    is_running: bool = False
    generation_frequency_days: int
    max_recommendations: int
    # Learning Spotlight global cycle anchor (server-managed; null until first enable).
    cycle_start_date: date | None = None
    # Learning Spotlight 5-day cycle order configuration.
    cycle_configuration: dict[str, Any] | None = None
    # Pending cycle configuration staged for next cycle (null when no pending changes).
    next_cycle_configuration: dict[str, Any] | None = None
    # Current active cycle strategy (e.g. "leading_thinker") and day (1-5).
    current_cycle: str | None = None
    current_cycle_day: int | None = None
    # Learning Spotlight number of daily papers (minimum 1, default 1; no upper limit).
    learning_spotlight_papers_count: int = 1
    # When true, users receive Learning Spotlight push notifications.
    is_pushnotification_enabled: bool = True
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
    cycle_configuration: dict[str, Any] | None = None
    learning_spotlight_papers_count: int | None = Field(
        default=None,
        description="Number of daily Learning Spotlight papers (minimum 1, no upper limit).",
    )
    is_pushnotification_enabled: bool | None = Field(
        default=None,
        description="When true, users receive Learning Spotlight push notifications.",
    )

    @field_validator("learning_spotlight_papers_count")
    @classmethod
    def validate_papers_count(cls, value: int | None) -> int | None:
        if value is not None and value < 1:
            raise PydanticCustomError(
                "greater_than_equal",
                "must be at least 1",
            )
        return value

    @field_validator("cycle_configuration")
    @classmethod
    def validate_cycle_cfg(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        from apps.learningspotlight.services.cycle_service import validate_cycle_configuration

        validated = validate_cycle_configuration(value)
        return {"cycle": [t.value for t in validated]}



class FeatureFlagItem(BaseModel):
    id: UUID
    key: str
    name: str
    description: str | None = None
    is_enabled: bool
    created_at: datetime
    updated_at: datetime


class FeatureFlagListData(BaseModel):
    items: list[FeatureFlagItem]


class FeatureFlagCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    key: str = Field(
        min_length=1,
        max_length=100,
        validation_alias=AliasChoices("key", "feature_key"),
    )
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    is_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("is_enabled", "enabled"),
    )

    @field_validator("key")
    @classmethod
    def normalize_key(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("key cannot be blank")
        return normalized

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name cannot be blank")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class AdminActivityLogItem(BaseModel):
    id: UUID
    user_id: UUID
    user_name: str | None = None
    role: str
    action: str
    module: str
    record_id: UUID | None = None
    description: str | None = None
    metadata: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class FeatureFlagUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    key: str = Field(
        min_length=1,
        max_length=100,
        validation_alias=AliasChoices("key", "feature_key"),
    )
    is_enabled: bool = Field(validation_alias=AliasChoices("is_enabled", "enabled"))

    @field_validator("key")
    @classmethod
    def normalize_key(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("key cannot be blank")
        return normalized


from enum import Enum


class TemplateStatusEnum(str, Enum):
    active = "active"
    inactive = "inactive"


class TemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    subject: str = Field(min_length=1, max_length=255)
    body_html: str = Field(min_length=1)
    status: TemplateStatusEnum = TemplateStatusEnum.active

    @field_validator("name", "subject", "body_html")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("Field cannot be blank")
        return value.strip()


class TemplateUpdate(BaseModel):
    template_id: UUID
    subject: str | None = Field(default=None, max_length=255)
    body_html: str | None = None
    status: TemplateStatusEnum | None = None

    @field_validator("subject", "body_html")
    @classmethod
    def validate_optional_non_empty(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Field cannot be blank")
        return value.strip() if value is not None else None


class TemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    subject: str
    body_html: str
    updated_by: UUID | None = None
    status: str
    created_at: datetime
    updated_at: datetime


class TemplateListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    subject: str
    updated_by: UUID | None = None
    status: str
    created_at: datetime
    updated_at: datetime


