from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from apps.profiles.db_models.profile_db_model import Profile
from apps.recommendations.services.recommendation_generation_service import (
    RecommendationGenerationService,
)
from apps.recommendations.services.recommendation_query_builder import collect_topics
from apps.recommendations.services.recommendation_settings_service import (
    RecommendationSettingsService,
)
from core.database.session import async_session_factory

logger = logging.getLogger(__name__)

GenerateDecisionAction = Literal["Generated", "Skipped"]


@dataclass(frozen=True, slots=True)
class _ProfileCandidate:
    user_id: UUID
    extracted_keywords: dict[str, Any]
    keywords_updated_at: datetime | None
    recommendations_updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class _GenerateDecision:
    should_generate: bool
    reason: str


@dataclass
class _CronStats:
    total_users: int = 0
    processed: int = 0
    generated: int = 0
    skipped: int = 0
    failed: int = 0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class RecommendationCronService:
    """Decide *when* learning recommendations should be generated.

    Generation itself is owned by ``RecommendationGenerationService``.
    Invoked by a scheduler later — not exposed as an API route and not
    scheduled here.
    """

    def __init__(
        self,
        *,
        settings_service: RecommendationSettingsService | None = None,
        generation_service: RecommendationGenerationService | None = None,
    ) -> None:
        self._settings_service = settings_service or RecommendationSettingsService()
        self._generation_service = (
            generation_service or RecommendationGenerationService()
        )

    @classmethod
    async def run_recommendation_generation(cls) -> bool:
        """Entry point the scheduler will call.

        Returns True when generation ran, False when skipped (disabled or unset).
        """
        return await cls()._run_recommendation_generation()

    async def _run_recommendation_generation(self) -> bool:
        async with async_session_factory() as session:
            if not await self._settings_service.is_cron_enabled(session):
                persisted = await self._settings_service.get_persisted_settings(session)
                if persisted is None:
                    logger.info(
                        "[recommendation-cron]\nCron skipped: recommendation "
                        "settings not configured by admin."
                    )
                else:
                    logger.info(
                        "[recommendation-cron]\nCron skipped: recommendation "
                        "generation disabled by admin."
                    )
                return False

            settings = await self._settings_service.get_persisted_settings(session)
            assert settings is not None  # guaranteed by is_cron_enabled
            generation_frequency_days = settings.generation_frequency_days
            max_recommendations = settings.max_recommendations
            profiles = await self._get_profiles(session)

        started = time.perf_counter()
        stats = _CronStats()
        stats.total_users = len(profiles)

        logger.info("[recommendation-cron]\nCron started")

        for profile in profiles:
            stats.processed += 1
            try:
                if not collect_topics(profile.extracted_keywords):
                    stats.skipped += 1
                    self._log_user_action(
                        user_id=profile.user_id,
                        action="Skipped",
                        reason="no searchable keywords",
                    )
                    continue

                decision = self._should_generate(
                    recommendations_updated_at=profile.recommendations_updated_at,
                    keywords_updated_at=profile.keywords_updated_at,
                    generation_frequency_days=generation_frequency_days,
                )
                if not decision.should_generate:
                    stats.skipped += 1
                    self._log_user_action(
                        user_id=profile.user_id,
                        action="Skipped",
                        reason=decision.reason,
                    )
                    continue

                logger.info(
                    "[recommendation-cron]\nuser_id=%s\naction=Eligible user",
                    profile.user_id,
                )

                async with async_session_factory() as session:
                    await self._generation_service.generate_for_user(
                        session,
                        profile.user_id,
                        max_recommendations,
                    )

                stats.generated += 1
                self._log_user_action(
                    user_id=profile.user_id,
                    action="Generated",
                    reason="recommendation generated",
                )
            except Exception:
                stats.failed += 1
                logger.exception(
                    "[recommendation-cron]\nuser_id=%s\naction=Failed",
                    profile.user_id,
                )

        self._log_summary(stats, duration_seconds=time.perf_counter() - started)
        return True

    async def _get_profiles(self, session: AsyncSession) -> list[_ProfileCandidate]:
        rows = (
            await session.execute(
                select(Profile).where(Profile.extracted_keywords.is_not(None))
            )
        ).scalars().all()

        return [
            _ProfileCandidate(
                user_id=row.user_id,
                extracted_keywords=dict(row.extracted_keywords or {}),
                keywords_updated_at=_as_utc(row.keywords_updated_at),
                recommendations_updated_at=_as_utc(row.recommendations_updated_at),
            )
            for row in rows
        ]

    def _should_generate(
        self,
        *,
        recommendations_updated_at: datetime | None,
        keywords_updated_at: datetime | None,
        generation_frequency_days: int,
        current_utc: datetime | None = None,
    ) -> _GenerateDecision:
        """Decide whether a profile needs a new recommendation snapshot."""
        now = current_utc or _utc_now()

        # CASE 1 — never generated
        if recommendations_updated_at is None:
            return _GenerateDecision(True, "recommendation generated")

        # CASE 2 — waiting for configured interval
        days_since_last_generation = (now.date() - recommendations_updated_at.date()).days
        if days_since_last_generation < generation_frequency_days:
            return _GenerateDecision(False, "waiting for configured interval")

        # CASE 3 — enough days, but keywords unchanged (or never set)
        if (
            keywords_updated_at is None
            or keywords_updated_at <= recommendations_updated_at
        ):
            return _GenerateDecision(False, "keywords unchanged")

        # CASE 4 — enough days and keywords changed after last generation
        return _GenerateDecision(True, "recommendation generated")

    def _log_user_action(
        self,
        *,
        user_id: UUID,
        action: GenerateDecisionAction,
        reason: str,
    ) -> None:
        if action == "Skipped":
            logger.info(
                "[recommendation-cron]\nuser_id=%s\naction=Skipped user\nreason=%s",
                user_id,
                reason,
            )
            return
        logger.info(
            "[recommendation-cron]\nuser_id=%s\naction=Generated recommendation\nreason=%s",
            user_id,
            reason,
        )

    def _log_summary(self, stats: _CronStats, *, duration_seconds: float) -> None:
        logger.info(
            "[recommendation-cron]\nCron completed\ntotal_users=%s\nprocessed=%s\n"
            "generated=%s\nskipped=%s\nfailed=%s\nduration_seconds=%.3f",
            stats.total_users,
            stats.processed,
            stats.generated,
            stats.skipped,
            stats.failed,
            duration_seconds,
        )
