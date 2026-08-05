from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator, ConfigDict

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
    is_read: bool
    read_at: datetime | None = None
    created_at: datetime


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
    values: list[str] = Field(..., min_length=1)

    @model_validator(mode="after")
    def validate_values(self) -> CreateCampaignTarget:
        cleaned = [str(value).strip() for value in self.values if value and str(value).strip()]
        if not cleaned:
            raise ValueError("values must contain at least one non-empty string")
        self.values = cleaned
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


class CampaignTargetResponse(BaseModel):
    """Same shape as create-campaign targets; returned as originally stored."""

    type: NotificationTargetType
    values: list[str]


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
    category_preferences: dict[str, bool] = Field(
        default_factory=dict,
        description=(
            "Dynamic mapping of notification category to enabled/disabled state."
        ),
        examples=[
            {
                "CONNECTION_REQUEST": True,
                "CONNECTION_ACCEPTED": True,
                "CONNECTION_DECLINED": True,
                "DIRECT_MESSAGE": False,
                "ANNOUNCEMENT": True,
                "TOPIC": True,
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
    category_preferences: dict[str, bool] | None = Field(
        default=None,
        description=(
            "Dynamic mapping of notification category to enabled/disabled state."
        ),
        examples=[
            {
                "CONNECTION_REQUEST": True,
                "CONNECTION_ACCEPTED": True,
                "CONNECTION_DECLINED": True,
                "DIRECT_MESSAGE": False,
                "ANNOUNCEMENT": True,
                "TOPIC": True,
            }
        ],
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "push_enabled": True,
                "in_app_enabled": True,
                "category_preferences": {
                    "CONNECTION_REQUEST": True,
                    "CONNECTION_ACCEPTED": True,
                    "CONNECTION_DECLINED": True,
                    "DIRECT_MESSAGE": False,
                    "ANNOUNCEMENT": True,
                    "TOPIC": True,
                },
            }
        }
    )



class NotificationPreferencesResponse(ApiResponse):
    data: NotificationPreferencesData | None = None


# Temporary test-only schema — remove with /test/push endpoint.
class TestPushRequest(BaseModel):
    title: str = Field(default="Test notification", max_length=200)
    body: str = Field(default="This is a temporary test push from KampuLynk.", max_length=1000)
    fcm_token: str | None = Field(
        default=None,
        description="Optional raw FCM token. When omitted, uses the current user's active installation tokens.",
    )


class TestPushResponse(ApiResponse):
    data: dict[str, Any] | None = None
