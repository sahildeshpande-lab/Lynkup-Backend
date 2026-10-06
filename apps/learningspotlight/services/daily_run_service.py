"""Durable UTC business-date completion for Learning Spotlight daily runs."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from apps.learningspotlight.db_models.learning_spotlight_daily_run_db_model import (
    LearningSpotlightDailyRun,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def fetch_completed_daily_run(
    session: AsyncSession,
    business_date: date,
) -> LearningSpotlightDailyRun | None:
    stmt = select(LearningSpotlightDailyRun).where(
        LearningSpotlightDailyRun.business_date == business_date
    )
    return (await session.execute(stmt)).scalars().first()


async def record_daily_run_completion(
    session: AsyncSession,
    *,
    business_date: date,
    cycle_day: int | None,
    spotlight_type: str | None,
) -> bool:
    """Insert a unique completion row. Returns False if the business date exists."""
    session.add(
        LearningSpotlightDailyRun(
            business_date=business_date,
            cycle_day=cycle_day,
            spotlight_type=spotlight_type,
            status="completed",
            completed_at=_utc_now(),
        )
    )
    try:
        await session.commit()
        return True
    except IntegrityError:
        await session.rollback()
        return False
