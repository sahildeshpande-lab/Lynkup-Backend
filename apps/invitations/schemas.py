from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from apps.invitations.config import INVITATION_CODE_MAX_LENGTH
from common.schemas import ApiResponse

# ABC1234 — generated invitation codes only (POST /invitations).
INVITATION_CODE_PATTERN = r"^[A-Za-z]{3}[0-9]{4}$"


class ValidateInvitationRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=INVITATION_CODE_MAX_LENGTH)


class AssociateInvitationRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=INVITATION_CODE_MAX_LENGTH)


class SoftDeleteInvitationRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=INVITATION_CODE_MAX_LENGTH)


class RedeemInvitationRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=INVITATION_CODE_MAX_LENGTH)


class InvitationCreateData(BaseModel):
    id: UUID
    code: str
    expires_at: datetime


class InvitationCreateResponse(ApiResponse):
    data: InvitationCreateData | None = None


class InvitationAssociateData(BaseModel):
    id: UUID
    code: str
    status: str
    redeemed_by_user_id: UUID


class InvitationAssociateResponse(ApiResponse):
    data: InvitationAssociateData | None = None


class InvitationValidateData(BaseModel):
    code: str
    status: str


class InvitationValidateResponse(ApiResponse):
    data: InvitationValidateData | None = None


class InvitationRedeemData(BaseModel):
    code: str
    inviter_user_id: UUID | None = None
    redeemed_by_user_id: UUID
    redemption_count: int
    is_converted: bool


class InvitationRedeemResponse(ApiResponse):
    data: InvitationRedeemData | None = None


class AdminInvitationItem(BaseModel):
    code: str
    user_id: UUID | None
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
