from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_serializer, model_validator, ConfigDict

from common.enums import (
    NotificationCampaignStatus,
    NotificationCampaignType,
    NotificationTargetType,
)
from common.schemas import ApiResponse


class NotificationItem(BaseModel):
    id: UUID
    notification_type: str | None = None
    notification_type_id: UUID
    campaign_id: UUID | None = None
    title: str
    body: str
    deep_link_payload: dict[str, Any] | None = None
    post_id: str | None = None
    is_read: bool
    read_at: datetime | None = None
    created_at: datetime


class NotificationListReason(BaseModel):
    """Present when in-app notifications are disabled; otherwise empty."""

    code: str | None = Field(
        default=None,
        description="Machine-readable reason code when notifications are hidden.",
        examples=["IN_APP_NOTIFICATIONS_DISABLED"],
    )
    message: str | None = Field(
        default=None,
        description="Human-readable explanation when notifications are hidden.",
        examples=["In-app notifications are turned off. Please turn them on to view your available notifications."],
    )


class NotificationListResponse(ApiResponse):
    data: dict | None = None


class MarkNotificationReadResponse(ApiResponse):
    data: NotificationItem | None = None


class MarkAllNotificationsReadResponse(ApiResponse):
    data: dict | None = None


class CreateNotificationRequest(BaseModel):
    recipient_user_id: UUID
    notification_type_id: UUID
    title: str = Field(..., max_length=255)
    body: str
    deep_link_payload: dict[str, Any] | None = None
    campaign_id: UUID | None = None


class CreateNotificationResponse(ApiResponse):
    data: NotificationItem | None = None


class CreateCampaignTarget(BaseModel):
    type: NotificationTargetType
    to_all: bool = False
    values: list[str] = Field(default_factory=list)
    is_alumni: bool | None = Field(
        default=None,
        description=(
            "When true, restrict recipients to profiles with is_alumni set. "
            "Omit or false for no alumni filter. Use with to_all=true to target "
            "all matching users of that type."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_target(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "values" not in data or data["values"] is None:
                data = {**data, "values": []}
        return data

    @model_validator(mode="after")
    def validate_values(self) -> CreateCampaignTarget:
        cleaned = [str(value).strip() for value in self.values if value and str(value).strip()]
        self.values = cleaned
        if not self.to_all and not self.values:
            raise ValueError("values must contain at least one non-empty string when to_all is false")
        return self


class CreateCampaignRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    message: str = Field(..., min_length=1)
    campaign_type: NotificationCampaignType
    targets: list[CreateCampaignTarget] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_null_targets(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("targets") is None:
            data = {**data, "targets": []}
        return data

    @model_validator(mode="after")
    def validate_targeting(self) -> CreateCampaignRequest:
        if self.campaign_type == NotificationCampaignType.announcement:
            if self.targets:
                raise ValueError(
                    "targets must be omitted or empty when campaign_type is ANNOUNCEMENT"
                )
            return self

        if self.campaign_type == NotificationCampaignType.topic:
            if not self.targets:
                raise ValueError("targets is required when campaign_type is TOPIC")
        return self


class UpdateCampaignRequest(CreateCampaignRequest):
    id: UUID


class DeleteCampaignRequest(BaseModel):
    id: UUID


class CreateCampaignData(BaseModel):
    id: UUID


class CreateCampaignResponse(ApiResponse):
    data: CreateCampaignData | None = None


class UpdateCampaignResponse(ApiResponse):
    data: CreateCampaignData | None = None


class DeleteCampaignResponse(ApiResponse):
    data: CreateCampaignData | None = None


class CampaignTargetUserValue(BaseModel):
    """Enriched user target entry for admin campaign list responses."""

    id: str
    firstName: str = ""
    lastName: str = ""


class CampaignTargetResponse(BaseModel):
    """Campaign targets as stored, with display enrichment on read.

    For USERS targets, ``values`` are objects with id/firstName/lastName.
    Other target types keep string values (IDs resolved to labels when possible).
    """

    type: NotificationTargetType
    to_all: bool = False
    values: list[str | CampaignTargetUserValue] = Field(default_factory=list)
    is_alumni: bool | None = None

    @model_serializer(mode="wrap")
    def _omit_null_is_alumni(self, serializer):
        data = serializer(self)
        if data.get("is_alumni") is None:
            data.pop("is_alumni", None)
        return data


class AdminCampaignListItem(BaseModel):
    id: UUID
    title: str
    message: str
    campaign_type: NotificationCampaignType
    status: NotificationCampaignStatus
    recipient_count: int
    targets: list[CampaignTargetResponse] = Field(default_factory=list)
    scheduled_at: datetime | None = None
    sent_at: datetime | None = None
    created_at: datetime


class AdminCampaignListResponse(ApiResponse):
    data: dict | None = None


class SendCampaignRequest(BaseModel):
    campaign_id: UUID


class SendCampaignResponse(ApiResponse):
    data: dict | None = None


# class NotificationPreferencesData(BaseModel):
#     push_enabled: bool
#     in_app_enabled: bool
#     category_preferences: dict[str, bool] = Field(default_factory=dict)

class NotificationPreferencesData(BaseModel):
    push_enabled: bool
    in_app_enabled: bool
    email_preferences: dict[str, bool] = Field(
        default_factory=dict,
        description="Email delivery preferences for optional mail (bulk_email).",
        examples=[
            {
                "bulk_email": True,
            }
        ],
    )
    category_preferences: dict[str, bool] = Field(
        default_factory=dict,
        description=(
            "Dynamic mapping of notification category to enabled/disabled state. "
            "Includes weekly_lynkup_request_reminder for LynkUp pending-request push."
        ),
        examples=[
            {
                "CONNECTION_REQUEST": True,
                "CONNECTION_ACCEPTED": True,
                "CONNECTION_DECLINED": True,
                "DIRECT_MESSAGE": False,
                "ANNOUNCEMENT": True,
                "TOPIC": True,
                "weekly_lynkup_request_reminder": True,
            }
        ],
    )


# class UpdateNotificationPreferencesRequest(BaseModel):
#     push_enabled: bool | None = None
#     in_app_enabled: bool | None = None
#     category_preferences: dict[str, bool] | None = None
class UpdateNotificationPreferencesRequest(BaseModel):
    push_enabled: bool | None = Field(
        default=None,
        description="Enable or disable push notifications.",
    )
    in_app_enabled: bool | None = Field(
        default=None,
        description="Enable or disable in-app notifications.",
    )
    email_preferences: dict[str, bool] | None = Field(
        default=None,
        description=(
            "Partial update of email preferences. Supported keys: "
            "bulk_email. Unspecified keys are preserved."
        ),
        examples=[
            {
                "bulk_email": False,
            }
        ],
    )
    category_preferences: dict[str, bool] | None = Field(
        default=None,
        description=(
            "Dynamic mapping of notification category to enabled/disabled state. "
            "Use weekly_lynkup_request_reminder for pending LynkUp reminder push."
        ),
        examples=[
            {
                "CONNECTION_REQUEST": True,
                "CONNECTION_ACCEPTED": True,
                "CONNECTION_DECLINED": True,
                "DIRECT_MESSAGE": False,
                "ANNOUNCEMENT": True,
                "TOPIC": True,
                "weekly_lynkup_request_reminder": True,
            }
        ],
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "push_enabled": True,
                "in_app_enabled": True,
                "email_preferences": {
                    "bulk_email": True,
                },
                "category_preferences": {
                    "CONNECTION_REQUEST": True,
                    "CONNECTION_ACCEPTED": True,
                    "CONNECTION_DECLINED": True,
                    "DIRECT_MESSAGE": False,
                    "ANNOUNCEMENT": True,
                    "TOPIC": True,
                    "weekly_lynkup_request_reminder": True,
                },
            }
        }
    )

    @field_validator("email_preferences")
    @classmethod
    def validate_email_preference_keys(
        cls, value: dict[str, bool] | None
    ) -> dict[str, bool] | None:
        if value is None:
            return value
        from apps.notifications.email_preferences import ALLOWED_EMAIL_PREFERENCE_KEYS

        unknown = sorted(set(value) - ALLOWED_EMAIL_PREFERENCE_KEYS)
        if unknown:
            raise ValueError(
                "Unsupported email preference keys: "
                + ", ".join(unknown)
                + ". Allowed: "
                + ", ".join(sorted(ALLOWED_EMAIL_PREFERENCE_KEYS))
            )
        return value



class NotificationPreferencesResponse(ApiResponse):
    data: NotificationPreferencesData | None = None


class TestNotificationRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    message: str = Field(..., min_length=1)
    fcm: str = Field(
        ...,
        min_length=1,
        description="FCM registration token (Android) or APNs device token (iOS)",
    )
    platform: str | None = Field(
        default="android",
        description="Device platform: android (FCM) or ios (APNs). Defaults to android.",
    )


class TestNotificationResponse(ApiResponse):
    data: dict | None = None


class AdminActivityNotificationItem(BaseModel):
    id: UUID
    user_id: UUID
    user_name: str | None = None
    role: str
    action: str
    module: str
    record_id: UUID | None = None
    description: str | None = None
    metadata: dict[str, Any] | None = None
    is_read: bool
    read_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AdminNotificationListResponse(ApiResponse):
    data: dict | None = None


class MarkAdminNotificationReadResponse(ApiResponse):
    data: AdminActivityNotificationItem | None = None


class MarkAllAdminNotificationsReadResponse(ApiResponse):
    data: dict | None = None

