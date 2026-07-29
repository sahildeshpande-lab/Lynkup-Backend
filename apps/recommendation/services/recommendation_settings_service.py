from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from apps.profiles.db_models import LearningRecommendationSettings

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RecommendationSettingsService:
    """Manage singleton learning recommendation settings and cron decisions."""

    def __init__(self) -> None:
        self._logger = logger

    async def get_settings(self, session: AsyncSession) -> LearningRecommendationSettings:
        """Return the active settings row.

        If no row exists (unexpected), fall back to defaults without modifying
        the database.
        """
        stmt = select(LearningRecommendationSettings)
        settings = (await session.execute(stmt)).scalars().first()
        if settings is None:
            # Safe fallback defaults (migration should normally seed one row).
            settings = LearningRecommendationSettings(
                is_enabled=True,
                generation_frequency_days=14,
                max_recommendations=10,
                updated_by=None,
            )
        return settings

    async def update_settings(
        self,
        session: AsyncSession,
        *,
        admin_user_id: UUID,
        is_enabled: bool | None = None,
        generation_frequency_days: int | None = None,
        max_recommendations: int | None = None,
    ) -> LearningRecommendationSettings:
        """Update only provided fields and set updated_by/updated_at."""
        async with session.begin():
            # Treat as singleton.
            stmt = select(LearningRecommendationSettings)
            existing_settings = (await session.execute(stmt)).scalars().all()

            if not existing_settings:
                settings = LearningRecommendationSettings(
                    is_enabled=True,
                    generation_frequency_days=14,
                    max_recommendations=10,
                    updated_by=admin_user_id,
                )
            else:
                settings = existing_settings[0]
                # If multiple rows exist, remove extras to keep a singleton.
                for extra in existing_settings[1:]:
                    await session.delete(extra)

            if is_enabled is not None:
                settings.is_enabled = is_enabled
            if generation_frequency_days is not None:
                if generation_frequency_days < 1 or generation_frequency_days > 365:
                    raise ValueError("generation_frequency_days must be between 1 and 365")
                settings.generation_frequency_days = generation_frequency_days
            if max_recommendations is not None:
                if max_recommendations < 1 or max_recommendations > 50:
                    raise ValueError("max_recommendations must be between 1 and 50")
                settings.max_recommendations = max_recommendations

            settings.updated_by = admin_user_id
            settings.updated_at = _utc_now()

            session.add(settings)

        # Reload for accurate timestamps (optional, but keeps API consistent).
        self._logger.info(
            "[recommendation-settings] is_enabled=%s generation_frequency_days=%s max_recommendations=%s",
            settings.is_enabled,
            settings.generation_frequency_days,
            settings.max_recommendations,
        )
        return settings

    async def should_generate_recommendation(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        recommendations_updated_at: datetime | None,
        current_utc_date: date | None = None,
    ) -> bool:
        """Return whether the cron should generate for one user.

        Logs:
          [recommendation-cron]
          user_id=...
          days_since_last_generation=...
          should_generate=...
        """
        settings = await self.get_settings(session)
        current_utc_date = current_utc_date or _utc_now().date()

        if not settings.is_enabled:
            should_generate = False
            days_since_last_generation = 0
            self._logger.info(
                "[recommendation-cron] user_id=%s days_since_last_generation=%s should_generate=%s",
                user_id,
                days_since_last_generation,
                should_generate,
            )
            return should_generate

        if recommendations_updated_at is None:
            should_generate = True
            days_since_last_generation = 0
            self._logger.info(
                "[recommendation-cron] user_id=%s days_since_last_generation=%s should_generate=%s",
                user_id,
                days_since_last_generation,
                should_generate,
            )
            return should_generate

        last_date = recommendations_updated_at.date()
        days_since_last_generation = (current_utc_date - last_date).days
        should_generate = days_since_last_generation >= settings.generation_frequency_days

        self._logger.info(
            "[recommendation-cron] user_id=%s days_since_last_generation=%s should_generate=%s",
            user_id,
            days_since_last_generation,
            should_generate,
        )
        return should_generate

