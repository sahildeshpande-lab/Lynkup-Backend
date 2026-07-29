from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Column, DateTime, Integer
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class LearningRecommendationSettings(SQLModel, table=True):
    __tablename__ = "learning_recommendation_settings"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    is_enabled: bool = Field(default=True, sa_column=Column(Boolean, nullable=False))
    generation_frequency_days: int = Field(
        default=14, sa_column=Column(Integer, nullable=False)
    )
    max_recommendations: int = Field(default=10, sa_column=Column(Integer, nullable=False))

    updated_by: UUID | None = Field(
        default=None,
        foreign_key="users.id",
        nullable=True,
    )

    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


LearningRecommendationSettings.model_rebuild()

