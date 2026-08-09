from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Column, DateTime, Enum, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlmodel import Field, Relationship, SQLModel

from common.enums import NotificationCampaignStatus, NotificationCampaignType
from common.time import utc_now


class NotificationType(SQLModel, table=True):
    __tablename__ = "notification_types"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(sa_column=Column(String(100), nullable=False, unique=True))
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    is_active: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default=text("true")),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    campaigns: list["NotificationCampaign"] = Relationship(
        sa_relationship=relationship("NotificationCampaign", back_populates="notification_type")
    )
    notifications: list["Notification"] = Relationship(
        sa_relationship=relationship("Notification", back_populates="notification_type")
    )


class NotificationCategory(SQLModel, table=True):
    """Catalog of preference categories. Active rows drive default category_preferences."""

    __tablename__ = "notification_categories"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    code: str = Field(sa_column=Column(String(100), nullable=False, unique=True))
    name: str = Field(sa_column=Column(String(255), nullable=False))
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    is_active: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default=text("true")),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class NotificationPreference(SQLModel, table=True):
    __tablename__ = "notification_preferences"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", nullable=False)
    push_enabled: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default=text("true")),
    )
    in_app_enabled: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default=text("true")),
    )
    category_preferences: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    __table_args__ = (
        Index("ix_notification_preferences_user_id", "user_id", unique=True),
    )


class NotificationCampaign(SQLModel, table=True):
    __tablename__ = "notification_campaigns"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    notification_type_id: UUID = Field(foreign_key="notification_types.id", nullable=False)
    campaign_type: NotificationCampaignType = Field(
        sa_column=Column(
            Enum(
                NotificationCampaignType,
                name="notificationcampaigntype",
                values_callable=lambda enum_cls: [member.value for member in enum_cls],
            ),
            nullable=False,
        ),
    )
    title: str = Field(sa_column=Column(String(255), nullable=False))
    message: str = Field(sa_column=Column(Text, nullable=False))
    deep_link_payload: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )
    created_by_admin_id: UUID | None = Field(default=None, foreign_key="users.id", nullable=True)
    scheduled_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    sent_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    status: NotificationCampaignStatus = Field(
        default=NotificationCampaignStatus.draft,
        sa_column=Column(
            Enum(
                NotificationCampaignStatus,
                name="notificationcampaignstatus",
                values_callable=lambda enum_cls: [member.value for member in enum_cls],
            ),
            nullable=False,
        ),
    )
    is_active: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default=text("true")),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    notification_type: Optional["NotificationType"] = Relationship(
        sa_relationship=relationship("NotificationType", back_populates="campaigns")
    )
    audience: list["NotificationCampaignAudience"] = Relationship(
        sa_relationship=relationship("NotificationCampaignAudience", back_populates="campaign")
    )
    notifications: list["Notification"] = Relationship(
        sa_relationship=relationship("Notification", back_populates="campaign")
    )

    __table_args__ = (
        Index("ix_notification_campaigns_notification_type_id", "notification_type_id"),
    )


class NotificationCampaignAudience(SQLModel, table=True):
    __tablename__ = "notification_campaign_audience"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    campaign_id: UUID = Field(foreign_key="notification_campaigns.id", nullable=False)
    user_id: UUID = Field(foreign_key="users.id", nullable=False)
    is_read: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("false")),
    )
    read_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    campaign: Optional["NotificationCampaign"] = Relationship(
        sa_relationship=relationship("NotificationCampaign", back_populates="audience")
    )

    __table_args__ = (
        Index("ix_notification_campaign_audience_user_id", "user_id"),
        Index("ix_notification_campaign_audience_campaign_id", "campaign_id"),
    )


class Notification(SQLModel, table=True):
    __tablename__ = "notifications"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    recipient_user_id: UUID = Field(foreign_key="users.id", nullable=False)
    notification_type_id: UUID = Field(foreign_key="notification_types.id", nullable=False)
    campaign_id: UUID | None = Field(
        default=None,
        foreign_key="notification_campaigns.id",
        nullable=True,
    )
    title: str = Field(sa_column=Column(String(255), nullable=False))
    body: str = Field(sa_column=Column(Text, nullable=False))
    deep_link_payload: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )
    is_read: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("false")),
    )
    read_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    notification_type: Optional["NotificationType"] = Relationship(
        sa_relationship=relationship("NotificationType", back_populates="notifications")
    )
    campaign: Optional["NotificationCampaign"] = Relationship(
        sa_relationship=relationship("NotificationCampaign", back_populates="notifications")
    )

    __table_args__ = (
        Index("ix_notifications_recipient_user_id", "recipient_user_id"),
        Index("ix_notifications_notification_type_id", "notification_type_id"),
        Index("ix_notifications_campaign_id", "campaign_id"),
    )
