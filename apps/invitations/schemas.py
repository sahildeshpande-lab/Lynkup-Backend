from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from common.schemas import ApiResponse

# ABC1234 — 3 uppercase letters + remaining digits
INVITATION_CODE_PATTERN = r"^[A-Za-z]{3}[0-9]{4}$"


class ValidateInvitationRequest(BaseModel):
    code: str = Field(..., min_length=7, max_length=7, pattern=INVITATION_CODE_PATTERN)


class SoftDeleteInvitationRequest(BaseModel):
    code: str = Field(..., min_length=7, max_length=7, pattern=INVITATION_CODE_PATTERN)


class InvitationCreateData(BaseModel):
    id: UUID
    code: str
    expires_at: datetime


class InvitationCreateResponse(ApiResponse):
    data: InvitationCreateData | None = None


class InvitationValidateData(BaseModel):
    user_id: UUID
    first_name: str | None = None
    last_name: str | None = None
    profile_photo_url: str | None = None
    university: str | None = None
    bio: str | None = None


class InvitationValidateResponse(ApiResponse):
    data: InvitationValidateData | None = None


class AdminInvitationItem(BaseModel):
    code: str
    user_id: UUID
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    status: str
    expires_at: datetime
    deleted_at: datetime | None = None


class AdminInvitationListResponse(ApiResponse):
    data: dict | None = None


class SoftDeleteInvitationResponse(ApiResponse):
    data: dict | None = None
