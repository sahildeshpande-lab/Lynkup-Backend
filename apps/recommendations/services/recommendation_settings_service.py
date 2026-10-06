from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from apps.profiles.db_models import (
    LearningRecommendationSettings,
    LearningRecommendationSettingsLog,
)
from apps.profiles.db_models.profile_db_model import Profile
from apps.profiles.services.response_service import _compose_full_name
from common.pagination import build_paginated_response

logger = logging.getLogger(__name__)

DEFAULT_IS_ENABLED = True
DEFAULT_GENERATION_FREQUENCY_DAYS = 14
DEFAULT_MAX_RECOMMENDATIONS = 10
DEFAULT_IS_PUSHNOTIFICATION_ENABLED = True

_TRACKED_SETTINGS_FIELDS = (
    "is_enabled",
    "generation_frequency_days",
    "max_recommendations",
    "cycle_start_date",
    "cycle_configuration",
    "learning_spotlight_papers_count",
    "is_pushnotification_enabled",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _in_memory_defaults() -> LearningRecommendationSettings:
    """Non-persisted fallback for non-admin readers (e.g. cron)."""
    now = _utc_now()
    return LearningRecommendationSettings(
        is_enabled=DEFAULT_IS_ENABLED,
        is_running=False,
        generation_frequency_days=DEFAULT_GENERATION_FREQUENCY_DAYS,
        max_recommendations=DEFAULT_MAX_RECOMMENDATIONS,
        cycle_start_date=None,
        cycle_configuration=None,
        learning_spotlight_papers_count=1,
        is_pushnotification_enabled=DEFAULT_IS_PUSHNOTIFICATION_ENABLED,
        updated_by=None,
        created_at=now,
        updated_at=now,
    )


def _settings_snapshot(
    source: LearningRecommendationSettings | LearningRecommendationSettingsLog | dict[str, Any],
) -> dict[str, Any]:
    if isinstance(source, dict):
        snapshot = {field: source[field] for field in _TRACKED_SETTINGS_FIELDS if field in source}
        snapshot.setdefault("cycle_start_date", source.get("cycle_start_date"))
        snapshot.setdefault("cycle_configuration", source.get("cycle_configuration"))
        snapshot.setdefault("learning_spotlight_papers_count", source.get("learning_spotlight_papers_count", 1))
        snapshot.setdefault(
            "is_pushnotification_enabled",
            source.get("is_pushnotification_enabled", DEFAULT_IS_PUSHNOTIFICATION_ENABLED),
        )
        return snapshot
    return {
        "is_enabled": source.is_enabled,
        "generation_frequency_days": source.generation_frequency_days,
        "max_recommendations": source.max_recommendations,
        # Settings-log rows do not store cycle_start_date / cycle_configuration; treat as absent.
        "cycle_start_date": getattr(source, "cycle_start_date", None),
        "cycle_configuration": getattr(source, "cycle_configuration", None),
        "learning_spotlight_papers_count": getattr(source, "learning_spotlight_papers_count", 1),
        "is_pushnotification_enabled": getattr(
            source,
            "is_pushnotification_enabled",
            DEFAULT_IS_PUSHNOTIFICATION_ENABLED,
        ),
    }


def _initial_settings_snapshot() -> dict[str, Any]:
    """Baseline used before the first history log entry existed."""
    return {
        "is_enabled": DEFAULT_IS_ENABLED,
        "generation_frequency_days": DEFAULT_GENERATION_FREQUENCY_DAYS,
        "max_recommendations": DEFAULT_MAX_RECOMMENDATIONS,
        "cycle_start_date": None,
        "cycle_configuration": None,
        "learning_spotlight_papers_count": 1,
        "is_pushnotification_enabled": DEFAULT_IS_PUSHNOTIFICATION_ENABLED,
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


async def _sync_recommendation_feature_flag(
    session: AsyncSession,
    is_enabled: bool,
) -> None:
    """Keep the 'recommendation' feature flag in admin_configurations synchronized."""
    try:
        from apps.administration.db_models import AdminConfiguration
        from apps.administration.db_models.admin_configuration_db_model import utc_now
        from common.enums import AdminConfigurationType

        stmt = select(AdminConfiguration).where(
            AdminConfiguration.key == "recommendation",
            AdminConfiguration.configuration_type == AdminConfigurationType.FEATURE_FLAG,
        )
        result = await session.execute(stmt)
        flag = result.scalar_one_or_none()
        now = utc_now()
        if flag is not None:
            flag.is_enabled = is_enabled
            flag.updated_at = now
            session.add(flag)
        else:
            flag = AdminConfiguration(
                key="recommendation",
                name="Recommendation System",
                description="Learning Recommendations & Spotlight feature flag",
                configuration_type=AdminConfigurationType.FEATURE_FLAG,
                is_enabled=is_enabled,
                value=None,
                created_at=now,
                updated_at=now,
            )
            session.add(flag)
    except Exception as exc:
        logger.warning(
            "[recommendation-settings] Could not sync recommendation feature flag: %s",
            exc,
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
        *,
        for_update: bool = False,
    ) -> LearningRecommendationSettings | None:
        """Load the first settings row, deleting any accidental extras."""
        stmt = select(LearningRecommendationSettings)
        if for_update:
            stmt = stmt.with_for_update()
        rows = (await session.execute(stmt)).scalars().all()
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
            is_running=False,
            generation_frequency_days=DEFAULT_GENERATION_FREQUENCY_DAYS,
            max_recommendations=DEFAULT_MAX_RECOMMENDATIONS,
            cycle_start_date=None,
            is_pushnotification_enabled=DEFAULT_IS_PUSHNOTIFICATION_ENABLED,
            updated_by=admin_user_id,
            created_at=now,
            updated_at=now,
        )
        session.add(settings)
        await _sync_recommendation_feature_flag(session, DEFAULT_IS_ENABLED)
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

    async def is_push_notification_enabled(self, session: AsyncSession) -> bool:
        """True when admin settings allow Learning Spotlight push notifications."""
        settings = await self.get_persisted_settings(session)
        if settings is None:
            return DEFAULT_IS_PUSHNOTIFICATION_ENABLED
        return getattr(
            settings,
            "is_pushnotification_enabled",
            DEFAULT_IS_PUSHNOTIFICATION_ENABLED,
        )

    async def try_claim_manual_spotlight_run(
        self,
        session: AsyncSession,
    ) -> bool:
        """Atomically reserve a manual Learning Spotlight run (``is_running=True``).

        Returns True when the caller may queue Celery work. Returns False when a
        run is already in progress (or queued). When settings were never
        configured, returns True without mutating state.
        """
        settings = await self._load_singleton(session, for_update=True)
        if settings is None:
            return True
        if settings.is_running:
            await session.rollback()
            return False
        settings.is_running = True
        session.add(settings)
        await session.commit()
        return True

    async def set_is_running(
        self,
        session: AsyncSession,
        *,
        is_running: bool,
    ) -> None:
        """Persist the admin-visible Learning Spotlight run flag.

        Does not write settings history. Missing settings are left unchanged
        so the cron never creates the admin singleton.
        """
        settings = await self.get_persisted_settings(session)
        if settings is None:
            logger.warning(
                "Cannot persist learning_recommendation_settings.is_running=%s; no settings row",
                is_running,
            )
            return
        settings.is_running = is_running
        session.add(settings)
        await session.commit()

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

    async def count_settings_history(self, session: AsyncSession) -> int:
        stmt = select(func.count()).select_from(LearningRecommendationSettingsLog)
        return int((await session.execute(stmt)).scalar_one())

    async def get_settings_history(
        self,
        session: AsyncSession,
        *,
        offset: int = 0,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return history newest-first as change diffs with updater display names.

        Uses a single query with an outer join to profiles (no N+1).
        Each item is compared against the next-older log; the oldest item is
        compared against the initial default settings snapshot.

        When ``limit`` is set, one extra row is fetched so the last item on the
        page can still diff against the next-older log entry.
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
        if limit is not None:
            stmt = stmt.offset(max(offset, 0)).limit(limit + 1)
        rows = (await session.execute(stmt)).all()

        display_rows = rows if limit is None else rows[:limit]
        history: list[dict[str, Any]] = []
        for index, (log, first_name, last_name) in enumerate(display_rows):
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
        page: int | None = None,
        page_size: int | None = None,
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

        if page is not None and page_size is not None:
            total_items = await self.count_settings_history(session)
            offset = (page - 1) * page_size
            history_items = await self.get_settings_history(
                session,
                offset=offset,
                limit=page_size,
            )
            history = build_paginated_response(
                history_items,
                page,
                page_size,
                total_items,
            ).model_dump()
        else:
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
        cycle_configuration: dict[str, Any] | None = None,
        learning_spotlight_papers_count: int | None = None,
        is_pushnotification_enabled: bool | None = None,
        actor_role: str | None = None,
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
        previous_snapshot = _settings_snapshot(settings)

        if generation_frequency_days is not None:
            if generation_frequency_days < 1 or generation_frequency_days > 365:
                raise ValueError("generation_frequency_days must be between 1 and 365")
        if max_recommendations is not None:
            if max_recommendations < 1 or max_recommendations > 50:
                raise ValueError("max_recommendations must be between 1 and 50")
        if learning_spotlight_papers_count is not None:
            if learning_spotlight_papers_count < 1:
                raise ValueError("learning_spotlight_papers_count must be at least 1")
        if is_enabled is not None:
            settings.is_enabled = is_enabled
            await _sync_recommendation_feature_flag(session, is_enabled)

        if generation_frequency_days is not None:
            settings.generation_frequency_days = generation_frequency_days
        if max_recommendations is not None:
            settings.max_recommendations = max_recommendations
        if cycle_configuration is not None:
            from apps.learningspotlight.services.cycle_service import validate_cycle_configuration

            validated_types = validate_cycle_configuration(cycle_configuration)
            settings.cycle_configuration = {"cycle": [t.value for t in validated_types]}
        if learning_spotlight_papers_count is not None:
            settings.learning_spotlight_papers_count = learning_spotlight_papers_count
        if is_pushnotification_enabled is not None:
            settings.is_pushnotification_enabled = is_pushnotification_enabled

        now = _utc_now()
        # Learning Spotlight: first enable with no anchor starts the global cycle today.
        # Do not accept cycle_start_date from clients; do not reset on disable/re-enable.
        if is_enabled is True and settings.cycle_start_date is None:
            settings.cycle_start_date = now.date()

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
        from apps.administration.services.admin_activity_log_service import (
            create_admin_activity_log,
            format_field_changes,
        )

        new_snapshot = _settings_snapshot(settings)
        changes = format_field_changes(previous_snapshot, new_snapshot) or "settings"
        if changes.startswith(("enabled the", "disabled the")):
            log_desc = changes
        else:
            log_desc = f"updated Semantic Scholar to {changes}"
        await create_admin_activity_log(
            session,
            user_id=admin_user_id,
            role=actor_role,
            action="update",
            module="recommendation_settings",
            record_id=settings.id,
            description=log_desc,
            metadata={"old": previous_snapshot, "new": new_snapshot},
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


DEFAULT_CYCLE_CONFIG: dict[str, Any] = {
    "cycle": [
        "leading_thinker",
        "country_perspective",
        "influential_research",
        "latest_research",
        "beyond_your_field",
    ]
}


def settings_to_dict(settings: LearningRecommendationSettings) -> dict[str, Any]:
    """Serialize settings for API responses (PATCH keeps UUID for updated_by)."""
    raw_cfg = getattr(settings, "cycle_configuration", None)
    if raw_cfg is None:
        raw_cfg = DEFAULT_CYCLE_CONFIG

    active_cycle = raw_cfg.get("cycle") if isinstance(raw_cfg, dict) else None
    if not active_cycle:
        active_cycle = DEFAULT_CYCLE_CONFIG["cycle"]

    next_cycle = raw_cfg.get("next_cycle") if isinstance(raw_cfg, dict) else None
    next_cycle_cfg = {"cycle": next_cycle} if next_cycle else None

    current_cycle_name: str | None = None
    current_cycle_day: int | None = None
    cycle_start = getattr(settings, "cycle_start_date", None)
    if cycle_start is not None:
        try:
            from apps.learningspotlight.services.cycle_service import (
                get_cycle_day,
                get_spotlight_type,
            )

            current_cycle_day = get_cycle_day(cycle_start)
            spotlight_type = get_spotlight_type(current_cycle_day, {"cycle": active_cycle})
            current_cycle_name = (
                spotlight_type.value
                if hasattr(spotlight_type, "value")
                else str(spotlight_type)
            )
        except Exception:
            current_cycle_name = None
            current_cycle_day = None

    return {
        "is_enabled": settings.is_enabled,
        "is_running": getattr(settings, "is_running", False),
        "generation_frequency_days": settings.generation_frequency_days,
        "max_recommendations": settings.max_recommendations,
        "cycle_start_date": settings.cycle_start_date,
        "cycle_configuration": {"cycle": active_cycle},
        "next_cycle_configuration": next_cycle_cfg,
        "current_cycle": current_cycle_name,
        "current_cycle_day": current_cycle_day,
        "learning_spotlight_papers_count": getattr(settings, "learning_spotlight_papers_count", 1),
        "is_pushnotification_enabled": getattr(
            settings,
            "is_pushnotification_enabled",
            DEFAULT_IS_PUSHNOTIFICATION_ENABLED,
        ),
        "updated_by": settings.updated_by,
        "updated_at": settings.updated_at,
        "created_at": settings.created_at,
    }



