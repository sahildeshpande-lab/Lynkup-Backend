from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from apps.bulk_send.enums import EmailCampaignStatus, EmailDeliveryStatus
from common.schemas import ApiResponse


class AttachmentMeta(BaseModel):
    file_name: str = Field(..., min_length=1, max_length=255)
    storage_key: str = Field(..., min_length=1, max_length=512)
    content_type: str = Field(..., min_length=1, max_length=128)


class CreateBulkCampaignRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    subject: str = Field(..., min_length=1, max_length=255)
    body_html: str = Field(..., min_length=1)
    body_text: str | None = None
    user_ids: list[UUID] = Field(..., min_length=1)
    attachments: list[AttachmentMeta] = Field(default_factory=list)

    @field_validator("user_ids")
    @classmethod
    def dedupe_user_ids(cls, value: list[UUID]) -> list[UUID]:
        seen: set[UUID] = set()
        unique: list[UUID] = []
        for user_id in value:
            if user_id not in seen:
                seen.add(user_id)
                unique.append(user_id)
        if not unique:
            raise ValueError("user_ids must contain at least one recipient")
        return unique


class DeliveryStats(BaseModel):
    total: int = 0
    pending: int = 0
    processing: int = 0
    sent: int = 0
    failed: int = 0


class CampaignSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    subject: str
    status: EmailCampaignStatus
    created_by: UUID
    total_recipients: int
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class CampaignDetail(CampaignSummary):
    body_html: str
    body_text: str | None = None
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    delivery_stats: DeliveryStats


class CreateCampaignData(BaseModel):
    campaign_id: UUID
    status: EmailCampaignStatus
    total_recipients: int


class CreateCampaignResponse(ApiResponse):
    data: CreateCampaignData | None = None


class CampaignListResponse(ApiResponse):
    data: dict[str, Any] | None = None


class CampaignDetailResponse(ApiResponse):
    data: CampaignDetail | None = None


class AttachmentUploadData(BaseModel):
    file_name: str
    storage_key: str
    content_type: str


class AttachmentUploadResponse(ApiResponse):
    data: AttachmentUploadData | None = None


class DeliveryResult(BaseModel):
    success: bool
    sendgrid_message_id: str | None = None
    failure_reason: str | None = None
    retryable: bool = True
