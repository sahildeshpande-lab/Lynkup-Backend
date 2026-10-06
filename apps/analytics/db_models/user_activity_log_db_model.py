from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, Uuid
from sqlmodel import Field, SQLModel

from common.enums import UserActivityLogType


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class UserActivityLog(SQLModel, table=True):
    __tablename__ = "user_activity_logs"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    user_id: UUID = Field(
        sa_column=Column(
            Uuid(),
            ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )
    activity_log: UserActivityLogType = Field(
        sa_column=Column(String(64), nullable=False, index=True),
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
        Index("ix_user_activity_logs_created_at_user_id", "created_at", "user_id"),
        Index("ix_user_activity_logs_activity_log_created_at", "activity_log", "created_at"),
    )
