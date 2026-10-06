from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Column, Date, DateTime, Integer, String, UniqueConstraint
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class LearningSpotlightDailyRun(SQLModel, table=True):
    """Durable unique completion record for one UTC Spotlight business date."""

    __tablename__ = "learning_spotlight_daily_runs"
    __table_args__ = (
        UniqueConstraint(
            "business_date",
            name="uq_learning_spotlight_daily_runs_business_date",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    business_date: date = Field(sa_column=Column(Date, nullable=False))
    cycle_day: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    spotlight_type: str | None = Field(
        default=None,
        sa_column=Column(String(64), nullable=True),
    )
    status: str = Field(
        default="completed",
        sa_column=Column(String(32), nullable=False, server_default="completed"),
    )
    completed_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


LearningSpotlightDailyRun.model_rebuild()
