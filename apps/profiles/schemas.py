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


class EducationUpdateRequest(BaseModel):
    universityId: str | int | None = None
    major: str
    minor: Optional[str] = None
    educationLevel: EducationLevel
    graduationDate: Optional[date] = None


class EducationResponseData(BaseModel):
    user_id: str
    education: dict[str, Any]


class ProfileVisibilityRequest(BaseModel):
    profileVisibility: str = Field(pattern="^(public|connections_only|private)$")


class ReportUserRequest(BaseModel):
    reason: str
    description: Optional[str] = None
