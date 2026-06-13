from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Literal

from pydantic import BaseModel, EmailStr, Field, ConfigDict

from common.enums import EducationLevel, Role


class ApiResponse(BaseModel):
    status: bool = True
    message: str = "success"
    data: Any | None = None



class AdminUserCreateRequest(BaseModel):
    firstName: str
    lastName: str
    email: EmailStr
    role: Role
    status: str | None = None


class CamelModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class UserUpdate(CamelModel):
    model_config = ConfigDict(extra="forbid")

    firstName: str | None = None
    lastName: str | None = None
    email: EmailStr | None = None
    university_id: str | None = None
    major: str | None = None
    minor: str | None = None
    educationLevel: EducationLevel | None = None
    bio: str | None = Field(default=None, max_length=500)
    status: Literal["pending", "active", "suspicious_review", "suspended", "banned", "deleting"] | None = None
    academicInterests: list[str] | None = None
    graduationDate: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    location: str | None = None


AdminUserUpdateRequest = UserUpdate


class AdminUserActionRequest(BaseModel):
    id: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_at: datetime | None = None
