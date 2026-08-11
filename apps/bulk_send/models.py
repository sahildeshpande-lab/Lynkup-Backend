from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, Column, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from apps.bulk_send.enums import EmailCampaignStatus, EmailDeliveryStatus

_JSON = JSONB().with_variant(JSON(), "sqlite")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EmailCampaign(SQLModel, table=True):
    __tablename__ = "email_campaigns"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    name: str = Field(sa_column=Column(String(255), nullable=False))
    subject: str = Field(sa_column=Column(String(255), nullable=False))
    body_html: str = Field(sa_column=Column(Text, nullable=False))
    body_text: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    status: EmailCampaignStatus = Field(
        default=EmailCampaignStatus.queued,
        sa_column=Column(String(32), nullable=False, index=True),
    )
    # FK to users.id is enforced by the Alembic migration.
    created_by: UUID = Field(nullable=False, index=True)
    total_recipients: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default="0"),
    )
    attachments: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(_JSON, nullable=False),
    )
    started_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    completed_at: datetime | None = Field(
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


class EmailDelivery(SQLModel, table=True):
    __tablename__ = "email_deliveries"
    __table_args__ = (
        UniqueConstraint("campaign_id", "user_id", name="uq_email_deliveries_campaign_user"),
        Index("ix_email_deliveries_campaign_id_status", "campaign_id", "status"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    # FKs to email_campaigns.id / users.id are enforced by the Alembic migration.
    campaign_id: UUID = Field(nullable=False, index=True)
    user_id: UUID = Field(nullable=False, index=True)
    email: str = Field(sa_column=Column(String(320), nullable=False))
    status: EmailDeliveryStatus = Field(
        default=EmailDeliveryStatus.pending,
        sa_column=Column(String(32), nullable=False, index=True),
    )
    sendgrid_message_id: str | None = Field(
        default=None,
        sa_column=Column(String(255), nullable=True),
    )
    attempt_count: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default="0"),
    )
    last_attempt_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    delivered_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    failure_reason: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    metadata_: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", _JSON, nullable=False),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
