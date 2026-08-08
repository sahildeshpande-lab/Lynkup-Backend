from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Column, DateTime, Integer
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class LearningRecommendationSettingsLog(SQLModel, table=True):
    """Historical snapshots of admin recommendation settings changes."""

    __tablename__ = "learning_recommendation_settings_logs"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    is_enabled: bool = Field(sa_column=Column(Boolean, nullable=False))
    generation_frequency_days: int = Field(sa_column=Column(Integer, nullable=False))
    max_recommendations: int = Field(sa_column=Column(Integer, nullable=False))

    updated_by: UUID | None = Field(
        default=None,
        foreign_key="users.id",
        nullable=True,
    )

    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


LearningRecommendationSettingsLog.model_rebuild()
