from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from apps.profiles.db_models.profile_db_model import Profile
from apps.recommendation.services.recommendation_persistence_service import (
    RecommendationPersistenceService,
)
from apps.recommendation.services.recommendation_query_builder import (
    build_semantic_scholar_query,
    collect_topics,
)
from apps.recommendation.services.recommendation_settings_service import (
    RecommendationSettingsService,
)
from apps.recommendation.services.semantic_scholar_service import search_papers
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
    """Generate and persist learning recommendations for eligible profiles.

    Invoked by a scheduler later — not exposed as an API route and not
    scheduled here.
    """

    def __init__(
        self,
        *,
        settings_service: RecommendationSettingsService | None = None,
        persistence_service: RecommendationPersistenceService | None = None,
    ) -> None:
        self._settings_service = settings_service or RecommendationSettingsService()
        self._persistence_service = (
            persistence_service or RecommendationPersistenceService()
        )

    @classmethod
    async def run_recommendation_generation(cls) -> None:
        """Entry point the scheduler will call."""
        await cls()._run_recommendation_generation()

    async def _run_recommendation_generation(self) -> None:
        started = time.perf_counter()
        stats = _CronStats()

        logger.info("[recommendation-cron]\nCron Started")

        async with async_session_factory() as session:
            settings = await self._load_settings(session)

            if not settings.is_enabled:
                logger.info(
                    "[recommendation-cron]\nRecommendation generation is disabled."
                )
                self._log_summary(stats, duration_seconds=time.perf_counter() - started)
                return

            generation_frequency_days = settings.generation_frequency_days
            max_recommendations = settings.max_recommendations
            profiles = await self._get_profiles(session)

        stats.total_users = len(profiles)

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

                async with async_session_factory() as session:
                    await self._generate_for_user(
                        session,
                        user_id=profile.user_id,
                        extracted_keywords=profile.extracted_keywords,
                        max_recommendations=max_recommendations,
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

    async def _load_settings(self, session: AsyncSession) -> Any:
        return await self._settings_service.get_settings(
            session,
            create_if_missing=False,
        )

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

    async def _generate_for_user(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        extracted_keywords: dict[str, Any],
        max_recommendations: int,
    ) -> None:
        """Build query, fetch papers, and persist/archive via existing services."""
        query = build_semantic_scholar_query(extracted_keywords)
        if not query:
            raise ValueError(
                f"No searchable query from extracted_keywords for user_id={user_id}"
            )

        result, status_code = await search_papers(query, limit=max_recommendations)
        if status_code == 429:
            raise RuntimeError(
                f"Semantic Scholar rate limit exceeded for user_id={user_id}"
            )

        recommendation_json: dict[str, Any] = {
            "query": query,
            "status_code": status_code,
            "result": result,
        }
        await self._persistence_service.save_learning_recommendations(
            session=session,
            user_id=user_id,
            recommendation_json=recommendation_json,
        )

    def _log_user_action(
        self,
        *,
        user_id: UUID,
        action: GenerateDecisionAction,
        reason: str,
    ) -> None:
        logger.info(
            "[recommendation-cron]\nuser_id=%s\naction=%s\nreason=%s",
            user_id,
            action,
            reason,
        )

    def _log_summary(self, stats: _CronStats, *, duration_seconds: float) -> None:
        logger.info(
            "[recommendation-cron]\nCron Finished\ntotal_users=%s\nprocessed=%s\n"
            "generated=%s\nskipped=%s\nfailed=%s\nduration_seconds=%.3f",
            stats.total_users,
            stats.processed,
            stats.generated,
            stats.skipped,
            stats.failed,
            duration_seconds,
        )
