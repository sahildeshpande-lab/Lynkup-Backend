from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, Enum as SqlEnum, ForeignKey, Index, String, Text, Uuid, text
from sqlalchemy.orm import relationship
from sqlmodel import Field, Relationship, SQLModel

from common.enums import ReportEntityType

if TYPE_CHECKING:
    from apps.accounts.db_models import User


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ModerationHistory(SQLModel, table=True):
    """Immutable audit log of moderation actions across users, posts, and comments."""

    __tablename__ = "moderation_history"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    entity_type: ReportEntityType = Field(
        sa_column=Column(
            SqlEnum(ReportEntityType, name="reportentitytype", create_type=False),
            nullable=False,
        )
    )
    entity_id: UUID = Field(nullable=False)
    moderator_id: UUID | None = Field(
        default=None,
        sa_column=Column(
            Uuid(),
            ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    action: str = Field(sa_column=Column(String(64), nullable=False))
    comment: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    moderator: "User | None" = Relationship(
        sa_relationship=relationship("User", foreign_keys="ModerationHistory.moderator_id")
    )

    __table_args__ = (
        Index("ix_moderation_history_entity", "entity_type", "entity_id"),
        Index("ix_moderation_history_moderator_id", "moderator_id"),
        Index("ix_moderation_history_action", "action"),
        Index("ix_moderation_history_created_at_desc", text("created_at DESC")),
    )
