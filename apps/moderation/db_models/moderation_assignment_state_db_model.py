from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel

# Singleton row id for round-robin cursor state.
MODERATION_ASSIGNMENT_STATE_ID = UUID("00000000-0000-0000-0000-000000000001")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ModerationAssignmentState(SQLModel, table=True):
    __tablename__ = "moderation_assignment_state"

    id: UUID = Field(primary_key=True)
    last_assigned_moderator_id: UUID | None = Field(
        default=None,
        foreign_key="users.id",
        nullable=True,
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
