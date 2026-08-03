from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from apps.profiles.db_models import (
    LearningRecommendationSettings,
    LearningRecommendationSettingsLog,
)
from apps.profiles.db_models.profile_db_model import Profile
from apps.profiles.services.response_service import _compose_full_name

logger = logging.getLogger(__name__)

DEFAULT_IS_ENABLED = True
DEFAULT_GENERATION_FREQUENCY_DAYS = 14
DEFAULT_MAX_RECOMMENDATIONS = 10

_TRACKED_SETTINGS_FIELDS = (
    "is_enabled",
    "generation_frequency_days",
    "max_recommendations",
)


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


def _settings_snapshot(
    source: LearningRecommendationSettings | LearningRecommendationSettingsLog | dict[str, Any],
) -> dict[str, Any]:
    if isinstance(source, dict):
        return {field: source[field] for field in _TRACKED_SETTINGS_FIELDS}
    return {
        "is_enabled": source.is_enabled,
        "generation_frequency_days": source.generation_frequency_days,
        "max_recommendations": source.max_recommendations,
    }


def _initial_settings_snapshot() -> dict[str, Any]:
    """Baseline used before the first history log entry existed."""
    return {
        "is_enabled": DEFAULT_IS_ENABLED,
        "generation_frequency_days": DEFAULT_GENERATION_FREQUENCY_DAYS,
        "max_recommendations": DEFAULT_MAX_RECOMMENDATIONS,
    }


def _build_changes(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for field in _TRACKED_SETTINGS_FIELDS:
        previous_value = previous[field]
        new_value = current[field]
        if previous_value != new_value:
            changes.append(
                {
                    "field": field,
                    "previous_value": previous_value,
                    "new_value": new_value,
                }
            )
    return changes


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

    async def get_persisted_settings(
        self,
        session: AsyncSession,
    ) -> LearningRecommendationSettings | None:
        """Return the admin-persisted settings row, or None if never configured."""
        return await self._load_singleton(session)

    async def is_cron_enabled(self, session: AsyncSession) -> bool:
        """True only when an admin has persisted settings with ``is_enabled=True``."""
        settings = await self.get_persisted_settings(session)
        return settings is not None and settings.is_enabled

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

    async def _resolve_admin_name(
        self,
        session: AsyncSession,
        user_id: UUID | None,
    ) -> str | None:
        if user_id is None:
            return None
        row = (
            await session.execute(
                select(Profile.first_name, Profile.last_name).where(
                    Profile.user_id == user_id
                )
            )
        ).one_or_none()
        if row is None:
            return None
        first_name, last_name = row
        return _compose_full_name(first_name, last_name) or None

    async def get_settings_history(
        self,
        session: AsyncSession,
    ) -> list[dict[str, Any]]:
        """Return history newest-first as change diffs with updater display names.

        Uses a single query with an outer join to profiles (no N+1).
        Each item is compared against the next-older log; the oldest item is
        compared against the initial default settings snapshot.
        """
        stmt = (
            select(
                LearningRecommendationSettingsLog,
                Profile.first_name,
                Profile.last_name,
            )
            .outerjoin(
                Profile,
                Profile.user_id == LearningRecommendationSettingsLog.updated_by,
            )
            .order_by(LearningRecommendationSettingsLog.created_at.desc())
        )
        rows = (await session.execute(stmt)).all()

        history: list[dict[str, Any]] = []
        for index, (log, first_name, last_name) in enumerate(rows):
            if index + 1 < len(rows):
                previous_snapshot = _settings_snapshot(rows[index + 1][0])
            else:
                previous_snapshot = _initial_settings_snapshot()

            full_name = _compose_full_name(first_name, last_name)
            history.append(
                {
                    "id": log.id,
                    "updated_by": full_name or None,
                    "updated_at": log.created_at,
                    "changes": _build_changes(
                        previous_snapshot,
                        _settings_snapshot(log),
                    ),
                }
            )
        return history

    async def get_settings_with_history(
        self,
        session: AsyncSession,
        *,
        admin_user_id: UUID,
    ) -> dict[str, Any]:
        """Return current settings plus change-diff history for the admin GET API."""
        settings = await self.get_settings(
            session,
            admin_user_id=admin_user_id,
            create_if_missing=True,
        )
        current_settings = settings_to_dict(settings)
        current_settings["updated_by"] = await self._resolve_admin_name(
            session,
            settings.updated_by,
        )
        history = await self.get_settings_history(session)
        return {
            "current_settings": current_settings,
            "history": history,
        }

    async def update_settings(
        self,
        session: AsyncSession,
        *,
        admin_user_id: UUID,
        is_enabled: bool | None = None,
        generation_frequency_days: int | None = None,
        max_recommendations: int | None = None,
    ) -> LearningRecommendationSettings:
        """Update only provided fields (admin-only). Creates defaults if missing.

        Persists a snapshot of the resulting configuration into
        ``learning_recommendation_settings_logs``.
        """
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

        now = _utc_now()
        settings.updated_by = admin_user_id
        settings.updated_at = now
        session.add(settings)
        session.add(
            LearningRecommendationSettingsLog(
                is_enabled=settings.is_enabled,
                generation_frequency_days=settings.generation_frequency_days,
                max_recommendations=settings.max_recommendations,
                updated_by=admin_user_id,
                created_at=now,
            )
        )
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

        Requires admin-persisted settings with ``is_enabled=True``; never uses
        in-memory defaults.
        """
        current_utc_date = current_utc_date or _utc_now().date()

        if not await self.is_cron_enabled(session):
            days_since_last_generation = 0
            should_generate = False
        elif recommendations_updated_at is None:
            days_since_last_generation = 0
            should_generate = True
        else:
            settings = await self.get_persisted_settings(session)
            last_date = recommendations_updated_at.date()
            days_since_last_generation = (current_utc_date - last_date).days
            should_generate = (
                settings is not None
                and days_since_last_generation >= settings.generation_frequency_days
            )

        self._logger.info(
            "[recommendation-cron] user_id=%s days_since_last_generation=%s should_generate=%s",
            user_id,
            days_since_last_generation,
            should_generate,
        )
        return should_generate


def settings_to_dict(settings: LearningRecommendationSettings) -> dict[str, Any]:
    """Serialize settings for API responses (PATCH keeps UUID for updated_by)."""
    return {
        "is_enabled": settings.is_enabled,
        "generation_frequency_days": settings.generation_frequency_days,
        "max_recommendations": settings.max_recommendations,
        "updated_by": settings.updated_by,
        "updated_at": settings.updated_at,
        "created_at": settings.created_at,
    }
