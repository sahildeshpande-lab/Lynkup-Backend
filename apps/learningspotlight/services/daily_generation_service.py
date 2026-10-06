"""Daily Learning Spotlight generation orchestration (V2).

Executes once per day across eligible active users in configurable batches
(default: 50 users per batch).

Pipeline per daily run
-----------------------
1. Check admin recommendation settings (``is_enabled``, ``cycle_start_date``).
2. Compute today's global ``cycle_day`` (1-5) and ``spotlight_type`` ONCE.
   All users and batches in this daily run share the same category.
3. Fetch eligible active standard users (``user_roles.role_id`` = user role,
   ``users.status`` = active) in batches ordered by:
   ``recommendations_updated_at ASC NULLS FIRST, user_id ASC``.
   (Does NOT update ``recommendations_updated_at``, preserving V1 eligibility.)
4. Process each batch:
   - Candidate Generation (Step 6)
   - Candidate Filtering (Step 7)
   - Scoring & Ranking (Step 8)
   - Idempotent Persistence & Archiving (Step 9)
5. Return structured ``DailyGenerationResult`` summary.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Literal, Protocol
from uuid import UUID

from sqlalchemy import and_, func, not_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from apps.accounts.db_models.user_db_model import User, UserRole
from apps.administration.services.user_management_service import (
    _is_learning_spotlight_recommended_today,
    _learning_spotlight_recommended_today_sql,
)
from apps.learningspotlight.config import settings as spotlight_settings
from apps.learningspotlight.schemas import SpotlightCandidate, SpotlightUserContext
from apps.learningspotlight.services.candidate_filter_service import (
    CandidateFilterService,
)
from apps.learningspotlight.services.candidate_scoring_service import (
    CandidateScoringService,
)
from apps.learningspotlight.services.cycle_service import (
    CYCLE_DAY_TO_SPOTLIGHT_TYPE,
    LearningSpotlightCycleService,
    get_cycle_day,
    get_spotlight_type,
    has_spotlight_for_cycle_day,
)
from apps.learningspotlight.services.keyword_normalization_service import (
    ensure_keyword_vocabulary_loaded,
)
from apps.learningspotlight.services.language_detection_service import (
    LanguageDetectionService,
)
from apps.learningspotlight.services.spotlight_persistence_service import (
    SpotlightPersistenceService,
)
from apps.learningspotlight.services.spotlight_strategy import get_spotlight_strategy
from apps.profiles.db_models.country_db_model import Country
from apps.profiles.db_models.profile_db_model import Profile
from apps.recommendations.services.recommendation_query_builder import collect_topics
from apps.recommendations.services.recommendation_settings_service import (
    RecommendationSettingsService,
)
from common.enums import SpotlightType, UserStatus
from core.database.session import async_session_factory

logger = logging.getLogger(__name__)

# Standard app-user role (`roles.name = 'user'`). Admins/moderators/viewers are excluded.
STANDARD_USER_ROLE_ID = UUID("37797477-c66e-404a-bee9-3150254f52b1")


def _normalize_spotlight_paper_id(paper_id: str | None) -> str:
    if paper_id is None:
        return ""
    return str(paper_id).strip().lower()

LearningSpotlightRunMode = Literal["scheduled", "manual"]


def profile_stale_for_learning_spotlight_clause():
    """Profiles needing regeneration (keywords/profile newer than last spotlight)."""
    return or_(
        Profile.learning_spotlight_updated_at.is_(None),
        Profile.keywords_updated_at > Profile.learning_spotlight_updated_at,
    )


def profile_missing_learning_spotlight_today_clause(today: date | None = None):
    """Active users who do not have a spotlight recommended today (UTC), per admin list."""
    return not_(_learning_spotlight_recommended_today_sql(today))


def _active_standard_user_profile_stmt():
    """Profiles for active users assigned the standard ``user`` role."""
    return (
        select(Profile)
        .join(User, User.id == Profile.user_id)
        .join(UserRole, UserRole.user_id == User.id)
        .where(
            UserRole.role_id == STANDARD_USER_ROLE_ID,
            User.status == UserStatus.active,
            User.is_deleted.is_(False),
        )
    )


class SpotlightPaperGenerator(Protocol):
    """Boundary for per-user paper generation, scoring, and persistence."""

    async def generate_for_user(
        self,
        session: AsyncSession,
        user_id: UUID,
        *,
        spotlight_type: SpotlightType,
        cycle_day: int,
        today: date,
        papers_count: int = 1,
        force_regenerate: bool = False,
    ) -> bool: ...


class DefaultSpotlightPaperGenerator:
    """Production paper generator integrating Steps 5 through 9."""

    def __init__(
        self,
        *,
        filter_service: CandidateFilterService | None = None,
        scoring_service: CandidateScoringService | None = None,
        persistence_service: SpotlightPersistenceService | None = None,
    ) -> None:
        self._filter_service = filter_service or CandidateFilterService()
        self._scoring_service = scoring_service or CandidateScoringService()
        self._persistence_service = persistence_service or SpotlightPersistenceService()

    async def _collect_filtered_candidates(
        self,
        session: AsyncSession,
        user_id: UUID,
        *,
        strategy,
        context: SpotlightUserContext,
        spotlight_type: SpotlightType,
        today: date,
        needed: int,
    ) -> list[SpotlightCandidate]:
        """Fetch from Semantic Scholar with escalating limits until enough pass filters."""
        base_limit = spotlight_settings.learning_spotlight_candidate_limit
        max_attempts = spotlight_settings.learning_spotlight_fetch_escalation_attempts
        merged: list[SpotlightCandidate] = []
        seen_ids: set[str] = set()

        for attempt in range(max_attempts):
            search_limit = base_limit * (attempt + 1)
            fetch_context = context.model_copy(update={"search_limit": search_limit})
            raw_candidates = await strategy.get_candidates(fetch_context)
            if not raw_candidates:
                continue

            filter_result = await self._filter_service.filter_for_user(
                session,
                user_id,
                raw_candidates,
                context=context,
                reference_date=today,
            )
            for candidate in filter_result.candidates:
                paper_key = _normalize_spotlight_paper_id(candidate.paper_id)
                if not paper_key or paper_key in seen_ids:
                    continue
                seen_ids.add(paper_key)
                merged.append(candidate)

            logger.info(
                "[spotlight-generator] user=%s type=%s fetch_attempt=%d/%d "
                "search_limit=%d filtered_pool=%d needed=%d",
                user_id,
                spotlight_type.value,
                attempt + 1,
                max_attempts,
                search_limit,
                len(merged),
                needed,
            )
            if len(merged) >= needed:
                break

        return merged

    async def generate_for_user(
        self,
        session: AsyncSession,
        user_id: UUID,
        *,
        spotlight_type: SpotlightType,
        cycle_day: int,
        today: date,
        papers_count: int = 1,
        force_regenerate: bool = False,
    ) -> bool:
        """Generate, filter, rank, and persist a spotlight for one user.

        Returns True if a new spotlight was persisted, False if skipped.
        """
        profile_stmt = select(Profile).where(Profile.user_id == user_id)
        profile_res = await session.execute(profile_stmt)
        profile = profile_res.scalar_one_or_none()
        if profile is None:
            return False

        country_name = None
        if getattr(profile, "country_id", None):
            country_res = await session.execute(
                select(Country.name).where(Country.id == profile.country_id)
            )
            country_name = country_res.scalar_one_or_none()

        extracted_keywords = dict(profile.extracted_keywords or {})
        interest_names = [
            str(item).strip()
            for item in (extracted_keywords.get("interests") or [])
            if item is not None and str(item).strip() and not str(item).strip().isdigit()
        ]
        context = SpotlightUserContext(
            user_id=user_id,
            country=country_name,
            major=profile.major,
            minor=profile.minor,
            interests=interest_names,
            extracted_keywords=extracted_keywords,
        )

        needed = max(1, papers_count)
        strategy = get_spotlight_strategy(spotlight_type)
        filtered_candidates = await self._collect_filtered_candidates(
            session,
            user_id,
            strategy=strategy,
            context=context,
            spotlight_type=spotlight_type,
            today=today,
            needed=needed,
        )
        if len(filtered_candidates) < needed:
            logger.info(
                "[spotlight-generator] Insufficient filtered candidates for user %s "
                "(type=%s have=%d need=%d)",
                user_id,
                spotlight_type.value,
                len(filtered_candidates),
                needed,
            )
            return False

        ranking_result = self._scoring_service.score_and_rank_candidates(
            filtered_candidates,
            context,
            reference_date=today,
        )
        ranked = getattr(ranking_result, "ranked_candidates", None)
        if isinstance(ranked, list) and ranked:
            candidates_to_use = ranked
        elif getattr(ranking_result, "selected_candidate", None):
            candidates_to_use = [ranking_result.selected_candidate]
        else:
            candidates_to_use = []

        if not candidates_to_use:
            logger.debug(
                "[spotlight-generator] user=%s type=%s ranking produced no selected candidate "
                "filtered_count=%d",
                user_id,
                spotlight_type.value,
                len(filtered_candidates),
            )
            return False

        selected_candidates = candidates_to_use[:needed]

        _spotlight, is_new = await self._persistence_service.persist_selected_spotlight(
            session,
            user_id,
            candidates=selected_candidates,
            cycle_day=cycle_day,
            spotlight_type=spotlight_type,
            today=today,
            force_regenerate=force_regenerate,
        )
        return is_new





class NotImplementedSpotlightPaperGenerator:
    """Placeholder paper generator that raises NotImplementedError."""

    async def generate_for_user(
        self,
        session: AsyncSession,
        user_id: UUID,
        *,
        spotlight_type: SpotlightType,
        cycle_day: int,
        today: date,
        papers_count: int = 1,
        force_regenerate: bool = False,
    ) -> bool:
        raise NotImplementedError(
            "Learning Spotlight paper generation is not implemented yet "
            f"(user_id={user_id}, cycle_day={cycle_day}, type={spotlight_type.value})"
        )


@dataclass(frozen=True, slots=True)
class _ProfileCandidate:
    user_id: UUID
    extracted_keywords: dict[str, Any]
    learning_spotlight: dict[str, Any] | None
    learning_spotlight_updated_at: datetime | None
    recommendations_updated_at: datetime | None
    keywords_updated_at: datetime | None = None


@dataclass
class DailyGenerationResult:
    """Summary of one Learning Spotlight daily orchestration run."""

    ran: bool
    reason: str | None = None
    cycle_day: int | None = None
    spotlight_type: SpotlightType | None = None
    batch_size: int = 50
    total_selected: int = 0
    processed: int = 0
    eligible_users: int = 0
    skipped_users: int = 0
    generated_users: int = 0
    failed_users: int = 0
    batches_processed: int = 0
    candidates_not_found: int = 0
    already_generated_count: int = 0
    duration_seconds: float = 0.0
    skip_reasons: dict[str, int] = field(default_factory=dict)
    stale_users: int = 0
    stale_never_generated: int = 0
    stale_keywords_updated: int = 0
    run_mode: LearningSpotlightRunMode = "scheduled"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_today(today: date | datetime | None = None) -> date:
    if today is None:
        return _utc_now().date()
    if isinstance(today, datetime):
        if today.tzinfo is None:
            return today.replace(tzinfo=timezone.utc).date()
        return today.astimezone(timezone.utc).date()
    return today


class LearningSpotlightDailyGenerationService:
    """Orchestrate one daily Learning Spotlight run for all eligible users."""

    def __init__(
        self,
        *,
        settings_service: RecommendationSettingsService | None = None,
        paper_generator: SpotlightPaperGenerator | None = None,
        batch_size: int | None = None,
        session_factory=None,
    ) -> None:
        self._settings_service = settings_service or RecommendationSettingsService()
        self._paper_generator: SpotlightPaperGenerator = (
            paper_generator or DefaultSpotlightPaperGenerator()
        )
        self._batch_size = (
            batch_size
            if batch_size is not None
            else spotlight_settings.learning_spotlight_batch_size
        )
        self._session_factory = session_factory

    def _sessions(self):
        return self._session_factory or async_session_factory

    @classmethod
    async def run_daily_generation(
        cls,
        *,
        today: date | None = None,
        batch_size: int | None = None,
        session_factory=None,
        run_mode: LearningSpotlightRunMode = "scheduled",
    ) -> DailyGenerationResult:
        """Entry point a scheduler / admin hook can call."""
        return await cls(
            batch_size=batch_size,
            session_factory=session_factory,
        )._run_daily_generation(today=today, run_mode=run_mode)

    async def _count_manual_backfill_users(
        self,
        session: AsyncSession,
        today: date,
    ) -> tuple[int, int, int]:
        """Return (missing today, never generated, keywords newer than last spotlight)."""

        async def _count(extra_where) -> int:
            stmt = _active_standard_user_profile_stmt().where(extra_where)
            return int(
                (
                    await session.execute(
                        select(func.count()).select_from(stmt.subquery())
                    )
                ).scalar_one()
            )

        missing_today = profile_missing_learning_spotlight_today_clause(today)
        total = await _count(missing_today)
        never_generated = await _count(
            and_(missing_today, Profile.learning_spotlight_updated_at.is_(None))
        )
        keywords_updated = await _count(
            and_(
                missing_today,
                Profile.learning_spotlight_updated_at.is_not(None),
                Profile.keywords_updated_at.is_not(None),
                Profile.keywords_updated_at > Profile.learning_spotlight_updated_at,
            )
        )
        return total, never_generated, keywords_updated

    async def _run_daily_generation(
        self,
        *,
        today: date | None = None,
        run_mode: LearningSpotlightRunMode = "scheduled",
    ) -> DailyGenerationResult:
        run_today = _utc_today(today)
        started = time.perf_counter()
        batch_size = self._batch_size
        session_factory = self._sessions()

        async with session_factory() as session:
            settings = await self._settings_service.get_persisted_settings(session)
            if settings is None or not settings.is_enabled:
                reason = (
                    "recommendation settings not configured by admin"
                    if settings is None
                    else "recommendation generation disabled by admin"
                )
                logger.info("[learning-spotlight-daily]\nSkipped: %s", reason)
                return DailyGenerationResult(
                    ran=False,
                    reason=reason,
                    batch_size=batch_size,
                    duration_seconds=time.perf_counter() - started,
                )

            if settings.cycle_start_date is None:
                reason = "cycle_start_date is null; enable settings once to initialize"
                logger.warning(
                    "[learning-spotlight-daily]\nConfiguration error: %s",
                    reason,
                )
                return DailyGenerationResult(
                    ran=False,
                    reason=reason,
                    batch_size=batch_size,
                    duration_seconds=time.perf_counter() - started,
                )

            if type(session).__module__ != "unittest.mock":
                await ensure_keyword_vocabulary_loaded(session)
                LanguageDetectionService.preload()

            cycle_start = settings.cycle_start_date
            cycle_config = getattr(settings, "cycle_configuration", None)
            papers_count = getattr(settings, "learning_spotlight_papers_count", 1) or 1

        # Calculate global cycle day & spotlight type ONCE for the entire daily run
        cycle_day = get_cycle_day(cycle_start, run_today)
        spotlight_type = get_spotlight_type(cycle_day, cycle_config)

        result = DailyGenerationResult(
            ran=True,
            cycle_day=cycle_day,
            spotlight_type=spotlight_type,
            batch_size=batch_size,
            run_mode=run_mode,
        )

        if run_mode == "manual":
            async with session_factory() as session:
                (
                    result.stale_users,
                    result.stale_never_generated,
                    result.stale_keywords_updated,
                ) = await self._count_manual_backfill_users(session, run_today)

        logger.info(
            "[learning-spotlight-daily]\nRun started\nrun_mode=%s\ncycle_day=%s\nspotlight_type=%s\n"
            "papers_count=%d\nbatch_size=%d\ntoday=%s\nstale_users=%s",
            run_mode,
            cycle_day,
            spotlight_type.value,
            papers_count,
            batch_size,
            run_today.isoformat(),
            result.stale_users if run_mode == "manual" else "n/a",
        )

        attempted_user_ids: set[UUID] = set()

        while True:
            async with session_factory() as session:
                batch_candidates = await self._get_next_profile_batch(
                    session,
                    batch_size=batch_size,
                    exclude_user_ids=attempted_user_ids,
                    run_mode=run_mode,
                )

            if not batch_candidates:
                break  # All eligible active users have been processed

            result.batches_processed += 1
            result.total_selected += len(batch_candidates)

            for candidate in batch_candidates:
                attempted_user_ids.add(candidate.user_id)
                result.processed += 1

                try:
                    decision = self._eligibility_decision(
                        candidate,
                        cycle_day=cycle_day,
                        today=run_today,
                        spotlight_type=spotlight_type,
                        run_mode=run_mode,
                    )
                    if not decision.eligible:
                        result.skipped_users += 1
                        result.skip_reasons[decision.reason] = (
                            result.skip_reasons.get(decision.reason, 0) + 1
                        )
                        if decision.reason == "already generated for today":
                            result.already_generated_count += 1
                        logger.info(
                            "[learning-spotlight-daily]\nuser_id=%s\naction=Skipped\nreason=%s",
                            candidate.user_id,
                            decision.reason,
                        )
                        continue

                    result.eligible_users += 1
                    async with session_factory() as session:
                        generated = await self._paper_generator.generate_for_user(
                            session,
                            candidate.user_id,
                            spotlight_type=spotlight_type,
                            cycle_day=cycle_day,
                            today=run_today,
                            papers_count=papers_count,
                            force_regenerate=(
                                run_mode == "manual"
                                or _keywords_updated_after_spotlight(candidate)
                                or _spotlight_missing_or_before_today(
                                    candidate, run_today
                                )
                            ),
                        )

                    if generated:
                        result.generated_users += 1
                        from apps.learningspotlight.services.spotlight_notification_service import (
                            notify_learning_spotlight_recommended_best_effort,
                        )

                        await notify_learning_spotlight_recommended_best_effort(
                            candidate.user_id,
                            spotlight_type=spotlight_type,
                            cycle_day=cycle_day,
                        )
                        logger.info(
                            "[learning-spotlight-daily]\nuser_id=%s\naction=Generated\n"
                            "cycle_day=%s\nspotlight_type=%s",
                            candidate.user_id,
                            cycle_day,
                            spotlight_type.value,
                        )
                    else:
                        result.skipped_users += 1
                        result.candidates_not_found += 1
                        reason = (
                            "leading_thinker_unimplemented"
                            if spotlight_type == SpotlightType.leading_thinker
                            else "no_eligible_candidates"
                        )
                        result.skip_reasons[reason] = (
                            result.skip_reasons.get(reason, 0) + 1
                        )
                except Exception:
                    result.failed_users += 1
                    logger.exception(
                        "[learning-spotlight-daily]\nuser_id=%s\naction=Failed",
                        candidate.user_id,
                    )

        result.duration_seconds = time.perf_counter() - started
        logger.info(
            "[learning-spotlight-daily]\nRun completed\ncycle_day=%s\nspotlight_type=%s\n"
            "total_selected=%s\nprocessed=%s\neligible=%s\ngenerated=%s\nskipped=%s\n"
            "failed=%s\nbatches=%s\nduration_seconds=%.3f",
            result.cycle_day,
            result.spotlight_type.value if result.spotlight_type else "none",
            result.total_selected,
            result.processed,
            result.eligible_users,
            result.generated_users,
            result.skipped_users,
            result.failed_users,
            result.batches_processed,
            result.duration_seconds,
        )
        return result

    async def _get_next_profile_batch(
        self,
        session: AsyncSession,
        *,
        batch_size: int,
        exclude_user_ids: set[UUID],
        run_mode: LearningSpotlightRunMode = "scheduled",
    ) -> list[_ProfileCandidate]:
        """Fetch the next batch of active profiles ordered by recommendations_updated_at."""
        if "_get_active_profile_candidates" in self.__dict__:
            all_candidates = await self._get_active_profile_candidates(session)
            remaining = [c for c in all_candidates if c.user_id not in exclude_user_ids]
            return remaining[:batch_size]

        stmt = _active_standard_user_profile_stmt()
        if exclude_user_ids:
            stmt = stmt.where(Profile.user_id.not_in(exclude_user_ids))

        stmt = stmt.order_by(
            Profile.recommendations_updated_at.asc().nullsfirst(),
            Profile.user_id.asc(),
        ).limit(batch_size)

        rows = (await session.execute(stmt)).scalars().all()

        return [
            _ProfileCandidate(
                user_id=row.user_id,
                extracted_keywords=dict(row.extracted_keywords or {}),
                learning_spotlight=(
                    dict(row.learning_spotlight)
                    if isinstance(row.learning_spotlight, dict)
                    else None
                ),
                learning_spotlight_updated_at=row.learning_spotlight_updated_at,
                keywords_updated_at=row.keywords_updated_at,
                recommendations_updated_at=row.recommendations_updated_at,
            )
            for row in rows
        ]

    async def _get_active_profile_candidates(
        self,
        session: AsyncSession,
    ) -> list[_ProfileCandidate]:
        """Load all active standard-user profiles (can be overridden in tests)."""
        stmt = _active_standard_user_profile_stmt().order_by(
            Profile.recommendations_updated_at.asc().nullsfirst(),
            Profile.user_id.asc(),
        )
        rows = (await session.execute(stmt)).scalars().all()
        return [
            _ProfileCandidate(
                user_id=row.user_id,
                extracted_keywords=dict(row.extracted_keywords or {}),
                learning_spotlight=(
                    dict(row.learning_spotlight)
                    if isinstance(row.learning_spotlight, dict)
                    else None
                ),
                learning_spotlight_updated_at=row.learning_spotlight_updated_at,
                keywords_updated_at=row.keywords_updated_at,
                recommendations_updated_at=row.recommendations_updated_at,
            )
            for row in rows
        ]


    def _eligibility_decision(
        self,
        candidate: _ProfileCandidate,
        *,
        cycle_day: int,
        today: date,
        spotlight_type: SpotlightType,
        run_mode: LearningSpotlightRunMode = "scheduled",
    ) -> "_EligibilityDecision":
        if not collect_topics(candidate.extracted_keywords):
            return _EligibilityDecision(False, "no searchable keywords")

        if has_spotlight_for_cycle_day(
            candidate.learning_spotlight,
            cycle_day=cycle_day,
            today=today,
            spotlight_type=spotlight_type,
        ):
            # Retry when today's DB timestamp is missing/old, or keywords changed
            # after the last spotlight (keeps academics current for any cycle).
            if _spotlight_missing_or_before_today(candidate, today):
                return _EligibilityDecision(True, "eligible_spotlight_not_updated_today")
            if _keywords_updated_after_spotlight(candidate):
                return _EligibilityDecision(True, "eligible_stale_keywords")
            return _EligibilityDecision(False, "already generated for today")

        return _EligibilityDecision(True, "eligible")


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _profile_candidate_missing_today(
    candidate: _ProfileCandidate, today: date
) -> bool:
    """True when admin would list the user as without today's recommendation."""
    return not _is_learning_spotlight_recommended_today(
        candidate.learning_spotlight_updated_at,
        today=today,
    )


def _profile_candidate_is_stale(candidate: _ProfileCandidate) -> bool:
    """True when the spotlight is missing or keywords changed after it was generated.

    Same timestamps are current. ``keywords_updated_at`` must be strictly newer.
    """
    spotlight_at = _as_utc(getattr(candidate, "learning_spotlight_updated_at", None))
    if spotlight_at is None:
        return True
    return _keywords_updated_after_spotlight(candidate)


def _keywords_updated_after_spotlight(candidate: _ProfileCandidate) -> bool:
    """True only when keywords were explicitly updated after the last spotlight.

    Both timestamps must be present. Missing ``learning_spotlight_updated_at``
    is handled separately by ``_spotlight_missing_or_before_today``.
    """
    keywords_at = _as_utc(getattr(candidate, "keywords_updated_at", None))
    spotlight_at = _as_utc(getattr(candidate, "learning_spotlight_updated_at", None))
    if keywords_at is None or spotlight_at is None:
        return False
    return keywords_at > spotlight_at


def _spotlight_missing_or_before_today(
    candidate: _ProfileCandidate,
    today: date,
) -> bool:
    """True when the profile has no spotlight timestamp for the current UTC day."""
    spotlight_at = _as_utc(getattr(candidate, "learning_spotlight_updated_at", None))
    if spotlight_at is None:
        return True
    return spotlight_at.date() < today


@dataclass(frozen=True, slots=True)
class _EligibilityDecision:
    eligible: bool
    reason: str
