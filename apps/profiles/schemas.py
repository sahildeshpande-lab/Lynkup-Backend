from __future__ import annotations

from datetime import date
from typing import Any, Optional, Literal

from pydantic import BaseModel, Field, field_validator, EmailStr

from apps.invitations.config import INVITATION_CODE_MAX_LENGTH
from apps.profiles.graduation_date import parse_graduation_date
from common.schemas import ApiResponse


def _validate_graduation_date(value: object) -> date | None:
    return parse_graduation_date(value)


class ProfileUpdateRequest(BaseModel):
    bio: Optional[str] = Field(default=None, max_length=500)
    major: Optional[str] = None
    minor: Optional[str] = None
    academicInterests: Optional[list[str | int]] = None
    notificationPreferences: Optional[dict[str, Any]] = None
    profilePhotoUrl: Optional[str] = None
    bannerPhotoUrl: Optional[str] = None
    graduationDate: Optional[date] = Field(
        default=None,
        description="Optional expected graduation date (DD-MM-YYYY).",
    )
    welcomeMessage: Optional[str] = None

    @field_validator("graduationDate", mode="before")
    @classmethod
    def validate_graduation_date(cls, value: object) -> date | None:
        return _validate_graduation_date(value)





class ProfileVisibilityRequest(BaseModel):
    profileVisibility: Literal[
        "public",
        "connections_only",
        "private"
    ]


class ReportUserRequest(BaseModel):
    reason: str
    description: Optional[str] = None


class OnboardingRequest(BaseModel):
    profile_photo_key: Optional[str] = Field(None, description="S3 storage key returned by POST /uploads/image")
    banner_photo_key : Optional[str] = Field(None, description="S3 storage key returned by POST /uploads/image")
    country_id: Optional[str] = None
    university_id: str
    major: str
    minor: str | None = Field(
        default=None,
        description="Optional academic minor name. Omit, null, or empty to skip.",
    )
    major_id: int | None = Field(default=None, description="Catalog major id")
    minor_id: int | None = Field(
        default=None,
        description="Optional catalog minor id. Omit to skip a minor.",
    )
    education_level_id: int
    bio: Optional[str] = Field(None, max_length=500)
    academic_interests: list[str | int]
    graduationDate: Optional[date] = Field(
        default=None,
        description="Optional expected graduation date (DD-MM-YYYY).",
    )
    invitation_code: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=INVITATION_CODE_MAX_LENGTH,
        description="Optional invitation code to redeem during onboarding",
    )

    @field_validator("graduationDate", mode="before")
    @classmethod
    def validate_graduation_date(cls, value: object) -> date | None:
        return _validate_graduation_date(value)

    @field_validator("minor", mode="before")
    @classmethod
    def empty_minor_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value


class CompletenessWeightsUpdateRequest(BaseModel):
    bio: Optional[float] = None
    university: Optional[float] = None
    major: Optional[float] = None
    edu_level: Optional[float] = None
    first_name: Optional[float] = None
    last_name: Optional[float] = None
    email: Optional[float] = None
    profile_photo_url: Optional[float] = None
    interests: Optional[float] = None
    graduation_date: Optional[float] = None
    location: Optional[float] = None

class UpdateProfileMeRequest(BaseModel):
    firstName: str | None = None
    lastName: str | None = None
    universityId: str | None = None
    countryId: str | None = None
    major: str | None = None
    bio: str | None = None


class UpdateProfileRequest(BaseModel):
    firstName: str | None = None
    lastName: str | None = None
    email: EmailStr | None = None
    major: str | None = None
    minor: str | None = None
    major_id: int | None = None
    minor_id: int | None = None
    university_id: str | None = None
    country_id: str | None = None
    education_level_id: int | None = None
    academic_interests: list[str | int] | None = None
    profile_photo_key: str | None = None
    banner_photo_key: str | None = None
    bio: str | None = None
    graduationDate: Optional[date] = Field(
        default=None,
        description="Optional expected graduation date (DD-MM-YYYY).",
    )

    @field_validator("graduationDate", mode="before")
    @classmethod
    def validate_graduation_date(cls, value: object) -> date | None:
        return _validate_graduation_date(value)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        if not normalized:
            return None
        return normalized.lower()
