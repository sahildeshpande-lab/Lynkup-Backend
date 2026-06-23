from __future__ import annotations

from datetime import date
from typing import Any, Optional

from pydantic import BaseModel, Field

class ApiResponse(BaseModel):
    status: bool = True
    message: str = "success"
    data: Any | None = None


class ProfileUpdateRequest(BaseModel):
    bio: Optional[str] = Field(default=None, max_length=500)
    major: Optional[str] = None
    minor: Optional[str] = None
    academicInterests: Optional[list[str | int]] = None
    notificationPreferences: Optional[dict[str, Any]] = None
    profilePhotoUrl: Optional[str] = None
    bannerPhotoUrl: Optional[str] = None
    graduationDate: Optional[date] = None
    welcomeMessage: Optional[str] = None





class ProfileVisibilityRequest(BaseModel):
    profileVisibility: str = Field(pattern="^(public|connections_only|private)$")


class ReportUserRequest(BaseModel):
    reason: str
    description: Optional[str] = None


class OnboardingRequest(BaseModel):
    profile_photo_key: Optional[str] = Field(None, description="S3 storage key returned by POST /uploads/image")
    banner_photo_key : Optional[str] = Field(None, description="S3 storage key returned by POST /uploads/image")
    university_id: str
    major: str
    minor: Optional[str] = None
    education_level_id: int
    bio: Optional[str] = Field(None, max_length=500)
    academic_interests: list[str | int]


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
    major: str | None = None
    bio: str | None = None
