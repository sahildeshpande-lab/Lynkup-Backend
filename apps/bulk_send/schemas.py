from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing_extensions import Self

from apps.bulk_send.enums import BulkEmailTargetType, EmailCampaignStatus, EmailDeliveryStatus
from common.pagination import PaginatedResponse
from common.schemas import ApiResponse

_UUID_TARGET_TYPES = frozenset(
    {
        BulkEmailTargetType.UNIVERSITY,
        BulkEmailTargetType.COUNTRY,
        BulkEmailTargetType.USER,
    }
)


def _is_valid_uuid(value: str) -> bool:
    try:
        UUID(value)
        return True
    except (TypeError, ValueError, AttributeError):
        return False


class AttachmentMeta(BaseModel):
    file_name: str = Field(..., min_length=1, max_length=255)
    storage_key: str = Field(..., min_length=1, max_length=512)
    content_type: str = Field(..., min_length=1, max_length=128)


class BulkEmailTarget(BaseModel):
    type: BulkEmailTargetType = Field(
        ...,
        description=(
            "Audience filter type: MAJOR, MINOR, EDUCATION_LEVEL, UNIVERSITY, COUNTRY, "
            "INTEREST, HASHTAG, or USER."
        ),
    )
    to_all: bool = False
    values: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_target(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "values" not in data or data["values"] is None:
                data = {**data, "values": []}
        return data

    @model_validator(mode="after")
    def validate_target(self) -> Self:
        cleaned = [str(value).strip() for value in self.values if value and str(value).strip()]
        self.values = cleaned

        if self.type == BulkEmailTargetType.USER and self.to_all:
            raise ValueError(
                "USER targets cannot use to_all=true; omit USER or provide explicit user IDs"
            )

        if self.to_all and cleaned:
            raise ValueError("values must be empty when to_all is true")

        if not self.to_all and not cleaned:
            raise ValueError(
                "values must contain at least one non-empty string when to_all is false"
            )

        if self.type in _UUID_TARGET_TYPES and not self.to_all:
            invalid = [value for value in cleaned if not _is_valid_uuid(value)]
            if invalid:
                raise ValueError(
                    f"{self.type.value} values must be valid UUIDs; invalid: {', '.join(invalid)}"
                )

        return self


class CreateBulkCampaignRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "AI & DS Alumni Campaign",
                "subject": "Important Update",
                "body_html": "<p>Hello everyone!</p>",
                "body_text": "Hello everyone!",
                "is_alumni": True,
                "targets": [
                    {
                        "type": "MAJOR",
                        "to_all": False,
                        "values": ["1", "2"],
                    },
                    {
                        "type": "MINOR",
                        "to_all": False,
                        "values": ["10", "Data Science"],
                    },
                    {
                        "type": "EDUCATION_LEVEL",
                        "to_all": False,
                        "values": ["Bachelors", "Masters"],
                    },
                    {
                        "type": "UNIVERSITY",
                        "to_all": False,
                        "values": ["11111111-1111-1111-1111-111111111111"],
                    },
                    {
                        "type": "COUNTRY",
                        "to_all": False,
                        "values": ["22222222-2222-2222-2222-222222222222"],
                    },
                    {
                        "type": "INTEREST",
                        "to_all": False,
                        "values": ["33333333-3333-3333-3333-333333333333"],
                    },
                    {
                        "type": "HASHTAG",
                        "to_all": False,
                        "values": ["artificial-intelligence"],
                    },
                    {
                        "type": "USER",
                        "to_all": False,
                        "values": ["44444444-4444-4444-4444-444444444444"],
                    },
                ],
                "attachments": [
                    {
                        "file_name": "announcement.pdf",
                        "storage_key": "bulk-emails/announcement.pdf",
                        "content_type": "application/pdf",
                    }
                ],
            }
        }
    )

    name: str = Field(..., min_length=1, max_length=255)
    subject: str = Field(..., min_length=1, max_length=255)
    body_html: str = Field(..., min_length=1)
    body_text: str | None = None
    is_alumni: bool = Field(
        default=False,
        description=(
            "Global alumni filter applied after target resolution. "
            "true = alumni only; false = no alumni restriction."
        ),
    )
    targets: list[BulkEmailTarget] = Field(
        default_factory=list,
        description=(
            "Audience filters (MAJOR, MINOR, EDUCATION_LEVEL, UNIVERSITY, COUNTRY, "
            "INTEREST, HASHTAG, USER). Within a target, values are OR'd; across target "
            "types, filters are AND'd. Empty list (or only to_all=true targets) means "
            "all eligible users, subject to is_alumni. MAJOR/MINOR values are IDs or "
            "names; EDUCATION_LEVEL values are level IDs or names (e.g. Bachelors, "
            "Masters); UNIVERSITY, COUNTRY, and USER values must be UUIDs."
        ),
    )
    # Deprecated legacy fields — normalized into ``targets`` when targets is empty.
    to_all: bool = Field(
        default=False,
        deprecated=True,
        description="Deprecated. Prefer empty targets for an unrestricted audience.",
    )
    user_ids: list[UUID] = Field(
        default_factory=list,
        deprecated=True,
        description="Deprecated. Prefer targets with type USER.",
    )
    country_ids: list[UUID] = Field(
        default_factory=list,
        deprecated=True,
        description="Deprecated. Prefer targets with type COUNTRY.",
    )
    attachments: list[AttachmentMeta] = Field(
        default_factory=list,
        description="Optional attachment metadata from prior upload; omit or null when none.",
    )

    @field_validator("attachments", mode="before")
    @classmethod
    def attachments_optional(cls, value: Any) -> Any:
        return [] if value is None else value

    @field_validator("targets", mode="before")
    @classmethod
    def targets_optional(cls, value: Any) -> Any:
        return [] if value is None else value

    @field_validator("user_ids", mode="before")
    @classmethod
    def user_ids_optional(cls, value: Any) -> Any:
        return [] if value is None else value

    @field_validator("country_ids", mode="before")
    @classmethod
    def country_ids_optional(cls, value: Any) -> Any:
        return [] if value is None else value

    @model_validator(mode="after")
    def normalize_legacy_targeting(self) -> Self:
        """Normalize deprecated top-level fields into ``targets`` when needed."""
        seen_users: set[UUID] = set()
        unique_users: list[UUID] = []
        for user_id in self.user_ids:
            if user_id not in seen_users:
                seen_users.add(user_id)
                unique_users.append(user_id)
        self.user_ids = unique_users

        seen_countries: set[UUID] = set()
        unique_countries: list[UUID] = []
        for country_id in self.country_ids:
            if country_id not in seen_countries:
                seen_countries.add(country_id)
                unique_countries.append(country_id)
        self.country_ids = unique_countries

        if self.targets:
            return self

        # Legacy → targets. Empty targets + to_all (or neither) = unrestricted audience.
        if unique_users:
            self.targets.append(
                BulkEmailTarget(
                    type=BulkEmailTargetType.USER,
                    to_all=False,
                    values=[str(uid) for uid in unique_users],
                )
            )
        if unique_countries:
            self.targets.append(
                BulkEmailTarget(
                    type=BulkEmailTargetType.COUNTRY,
                    to_all=False,
                    values=[str(cid) for cid in unique_countries],
                )
            )
        return self


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
    body_text: str | None = None
    status: EmailCampaignStatus
    created_by: UUID
    total_recipients: int
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class CampaignRecipientDetail(BaseModel):
    user_id: UUID
    first_name: str | None = None
    last_name: str | None = None
    user_name: str | None = None
    email: str
    status: EmailDeliveryStatus
    is_delivered: bool = False
    delivered_at: datetime | None = None
    last_attempt_at: datetime | None = None
    failure_reason: str | None = None


class CampaignDetail(CampaignSummary):
    body_html: str
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    delivery_stats: DeliveryStats
    recipients: PaginatedResponse[CampaignRecipientDetail]


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


class BulkPersonalizationRecipient(BaseModel):
    """One SendGrid personalization target (one recipient)."""

    email: str
    delivery_id: UUID
    campaign_id: UUID


class BulkBatchSendResult(BaseModel):
    """Result of one SendGrid Mail Send request covering many personalizations."""

    success: bool
    sendgrid_message_id: str | None = None
    failure_reason: str | None = None
    retryable: bool = True
    recipient_count: int = 0
