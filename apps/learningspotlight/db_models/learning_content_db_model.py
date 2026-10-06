from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class LearningContent(SQLModel, table=True):
    """Stores generated learning content (e.g. non-LLM paper summaries, syntheses) per user and paper."""

    __tablename__ = "learning_content"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "paper_id",
            "content_type",
            name="uq_learning_content_user_paper_type",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    user_id: UUID = Field(foreign_key="profiles.user_id", nullable=False, index=True)
    paper_id: str = Field(sa_column=Column(String(255), nullable=False, index=True))
    content_type: str = Field(
        default="SUMMARY",
        sa_column=Column(String(50), nullable=False, index=True),
    )
    content: str = Field(sa_column=Column(Text, nullable=False))
    source_papers: list[str] | None = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


LearningContent.model_rebuild()
