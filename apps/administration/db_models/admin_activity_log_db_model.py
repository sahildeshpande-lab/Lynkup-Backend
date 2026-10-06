from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Column, DateTime, Index, String, Text, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AdminActivityLog(SQLModel, table=True):
    """Centralized audit trail for superadmin and moderator actions."""

    __tablename__ = "admin_activity_log"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(
        sa_column=Column(Uuid(), nullable=False, index=True),
    )
    role: str = Field(sa_column=Column(String(32), nullable=False))
    action: str = Field(sa_column=Column(String(64), nullable=False, index=True))
    module: str = Field(sa_column=Column(String(64), nullable=False, index=True))
    record_id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid(), nullable=True),
    )
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    log_metadata: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column("metadata", JSONB, nullable=True),
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
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            onupdate=utc_now,
        ),
    )

    __table_args__ = (
        Index("ix_admin_activity_log_user_id_created_at", "user_id", "created_at"),
        Index("ix_admin_activity_log_action_created_at", "action", "created_at"),
        Index("ix_admin_activity_log_module_created_at", "module", "created_at"),
        Index("ix_admin_activity_log_user_id_is_read", "user_id", "is_read"),
    )
