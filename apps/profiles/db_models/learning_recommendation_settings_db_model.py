from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Column, Date, DateTime, Integer
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel



def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class LearningRecommendationSettings(SQLModel, table=True):
    __tablename__ = "learning_recommendation_settings"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    is_enabled: bool = Field(default=True, sa_column=Column(Boolean, nullable=False))
    is_running: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default="false"),
    )
    generation_frequency_days: int = Field(
        default=14, sa_column=Column(Integer, nullable=False)
    )
    max_recommendations: int = Field(default=10, sa_column=Column(Integer, nullable=False))
    # Learning Spotlight global cycle anchor (NULL until first enable).
    cycle_start_date: date | None = Field(
        default=None,
        sa_column=Column(Date, nullable=True),
    )
    # Learning Spotlight 5-day cycle category order configuration (NULL defaults to standard order).
    cycle_configuration: dict | None = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )
    # Learning Spotlight number of daily papers to recommend (minimum 1, default 1; no upper limit).
    learning_spotlight_papers_count: int = Field(
        default=1,
        sa_column=Column(Integer, nullable=False, server_default="1"),
    )
    is_pushnotification_enabled: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default="true"),
    )



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
