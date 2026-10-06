from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID as PyUUID, uuid4

from sqlalchemy import Column, DateTime, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlmodel import Field, SQLModel

from common.enums import LearningPaperAction


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class LearningPaperInteraction(SQLModel, table=True):
    """Stores immutable event logs of user interactions on academic papers."""

    __tablename__ = "learning_paper_interactions"
    __table_args__ = (
        Index("ix_learning_paper_interactions_user_action_created", "user_id", "action", "created_at"),
        Index("ix_learning_paper_interactions_user_paper_created", "user_id", "paper_id", "created_at"),
    )

    id: PyUUID = Field(default_factory=uuid4, primary_key=True, index=True)
    user_id: PyUUID = Field(foreign_key="profiles.user_id", nullable=False, index=True)
    paper_id: str = Field(sa_column=Column(String(255), nullable=False, index=True))
    spotlight_id: PyUUID | None = Field(
        default=None,
        sa_column=Column(PgUUID(as_uuid=True), nullable=True, index=True),
    )
    action: str = Field(sa_column=Column(String(50), nullable=False, index=True))
    read_time_seconds: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=True),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


LearningPaperInteraction.model_rebuild()
