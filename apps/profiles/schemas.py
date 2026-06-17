from __future__ import annotations

from datetime import date
from typing import Any, Optional

from pydantic import BaseModel, Field

from common.enums import EducationLevel


class ApiResponse(BaseModel):
    status: bool = True
    message: str = "success"
    data: Any | None = None


class ProfileUpdateRequest(BaseModel):
    bio: Optional[str] = Field(default=None, max_length=500)
    major: Optional[str] = None
    minor: Optional[str] = None
    academicInterests: Optional[list[str]] = None
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
