from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from apps.profiles.db_models import LearningRecommendationSettings

logger = logging.getLogger(__name__)

DEFAULT_IS_ENABLED = True
DEFAULT_GENERATION_FREQUENCY_DAYS = 14
DEFAULT_MAX_RECOMMENDATIONS = 10


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _in_memory_defaults() -> LearningRecommendationSettings:
    """Non-persisted fallback for non-admin readers (e.g. cron)."""
    now = _utc_now()
    return LearningRecommendationSettings(
        is_enabled=DEFAULT_IS_ENABLED,
        generation_frequency_days=DEFAULT_GENERATION_FREQUENCY_DAYS,
        max_recommendations=DEFAULT_MAX_RECOMMENDATIONS,
        updated_by=None,
        created_at=now,
        updated_at=now,
    )


class RecommendationSettingsService:
    """Manage the singleton learning recommendation configuration row.

    Persisting or creating settings is admin-only. Cron/readers may read
    existing settings or use in-memory defaults without writing to the DB.
    """

    def __init__(self) -> None:
        self._logger = logger

    async def _load_singleton(
        self,
        session: AsyncSession,
    ) -> LearningRecommendationSettings | None:
        """Load the first settings row, deleting any accidental extras."""
        rows = (
            await session.execute(select(LearningRecommendationSettings))
        ).scalars().all()
        if not rows:
            return None

        settings = rows[0]
        for extra in rows[1:]:
            await session.delete(extra)
        if len(rows) > 1:
            await session.commit()
            await session.refresh(settings)
        return settings

    async def create_default_settings(
        self,
        session: AsyncSession,
        *,
        admin_user_id: UUID,
    ) -> LearningRecommendationSettings:
        """Insert the singleton default configuration (admin-only)."""
        now = _utc_now()
        settings = LearningRecommendationSettings(
            is_enabled=DEFAULT_IS_ENABLED,
            generation_frequency_days=DEFAULT_GENERATION_FREQUENCY_DAYS,
            max_recommendations=DEFAULT_MAX_RECOMMENDATIONS,
            updated_by=admin_user_id,
            created_at=now,
            updated_at=now,
        )
        session.add(settings)
        await session.commit()
        await session.refresh(settings)
        return settings

    async def get_settings(
        self,
        session: AsyncSession,
        *,
        admin_user_id: UUID | None = None,
        create_if_missing: bool = False,
    ) -> LearningRecommendationSettings:
        """Return the active configuration.

        When ``create_if_missing`` is True, a missing row is inserted and must
        be attributed to ``admin_user_id``. Non-admin callers never persist.
        """
        settings = await self._load_singleton(session)
        if settings is not None:
            return settings

        if create_if_missing:
            if admin_user_id is None:
                raise PermissionError(
                    "Only an authenticated admin can create recommendation settings"
                )
            return await self.create_default_settings(
                session,
                admin_user_id=admin_user_id,
            )

        return _in_memory_defaults()

    async def update_settings(
        self,
        session: AsyncSession,
        *,
        admin_user_id: UUID,
        is_enabled: bool | None = None,
        generation_frequency_days: int | None = None,
        max_recommendations: int | None = None,
    ) -> LearningRecommendationSettings:
        """Update only provided fields (admin-only). Creates defaults if missing."""
        settings = await self.get_settings(
            session,
            admin_user_id=admin_user_id,
            create_if_missing=True,
        )

        if generation_frequency_days is not None:
            if generation_frequency_days < 1 or generation_frequency_days > 365:
                raise ValueError("generation_frequency_days must be between 1 and 365")
        if max_recommendations is not None:
            if max_recommendations < 1 or max_recommendations > 50:
                raise ValueError("max_recommendations must be between 1 and 50")

        if is_enabled is not None:
            settings.is_enabled = is_enabled
        if generation_frequency_days is not None:
            settings.generation_frequency_days = generation_frequency_days
        if max_recommendations is not None:
            settings.max_recommendations = max_recommendations

        settings.updated_by = admin_user_id
        settings.updated_at = _utc_now()
        session.add(settings)
        await session.commit()
        await session.refresh(settings)
        return settings

    async def should_generate_recommendation(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        recommendations_updated_at: datetime | None,
        current_utc_date: date | None = None,
    ) -> bool:
        """Return whether recommendations should be regenerated for a user.

        Reads settings without creating DB rows (admin owns persistence).
        """
        settings = await self.get_settings(session, create_if_missing=False)
        current_utc_date = current_utc_date or _utc_now().date()

        if not settings.is_enabled:
            days_since_last_generation = 0
            should_generate = False
        elif recommendations_updated_at is None:
            days_since_last_generation = 0
            should_generate = True
        else:
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


def settings_to_dict(settings: LearningRecommendationSettings) -> dict[str, Any]:
    """Serialize settings for API responses."""
    return {
        "is_enabled": settings.is_enabled,
        "generation_frequency_days": settings.generation_frequency_days,
        "max_recommendations": settings.max_recommendations,
        "updated_at": settings.updated_at,
        "updated_by": settings.updated_by,
    }
