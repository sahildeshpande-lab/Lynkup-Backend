"""Learning Spotlight (V2) – shared daily generation runner.

Scheduled by Celery Beat at 00:00 UTC via ``kampulynk.spotlight.tick``
and also invoked directly by Superadmin ``POST /api/v1/admin/spotlight/runcron``.

Multi-replica safe: uses a dedicated PostgreSQL advisory lock on one physical
connection plus a unique UTC business-date completion row.

``learning_recommendation_settings.is_running`` is a persisted admin-visible
status flag. Manual ``/runcron`` sets it atomically before queueing; the
worker clears it in ``finally``. The advisory lock remains the cross-process
concurrency guard for actual generation.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from apps.learningspotlight.services.daily_generation_service import (
    DailyGenerationResult,
    LearningSpotlightDailyGenerationService,
    LearningSpotlightRunMode,
)
from apps.learningspotlight.services.daily_run_service import (
    fetch_completed_daily_run,
    record_daily_run_completion,
)
from core.database.session import async_session_factory

logger = logging.getLogger(__name__)

# Unique 64-bit advisory lock key for Learning Spotlight daily generation
_LEARNING_SPOTLIGHT_LOCK_KEY = 8_046_091_501

LearningSpotlightRunStatus = Literal[
    "disabled",
    "locked",
    "completed",
    "already_completed",
    "failed",
]


@dataclass
class LearningSpotlightRunOutcome:
    status: LearningSpotlightRunStatus
    result: DailyGenerationResult | None = None
    business_date: date | None = None
    run_mode: LearningSpotlightRunMode = "scheduled"


def resolve_spotlight_business_date(*, now: datetime | None = None) -> date:
    """Latest eligible UTC business date.

    A missed midnight schedule recovers this date once. Historical missed dates
    are not generated as a backlog.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).date()


def _manual_trigger_metadata(result: DailyGenerationResult) -> str | None:
    """Name the stale population without claiming a cause that did not occur."""
    never = result.stale_never_generated
    keywords = result.stale_keywords_updated
    if never > 0 and keywords > 0:
        return "updated_keywords_and_never_generated"
    if keywords > 0:
        return "updated_keywords"
    if never > 0:
        return "never_generated"
    return None


def _spotlight_type_title(result: DailyGenerationResult) -> str:
    if result.spotlight_type is None:
        return "Unknown"
    return result.spotlight_type.value.replace("_", " ").title()


def _build_activity_metadata(
    *,
    run_mode: LearningSpotlightRunMode,
    business_date: date,
    result: DailyGenerationResult,
) -> dict:
    spotlight_type_val = (
        result.spotlight_type.value if result.spotlight_type else None
    )
    metadata: dict = {
        "run_mode": run_mode,
        "business_date": business_date.isoformat(),
        "cycle_day": result.cycle_day,
        "spotlight_type": spotlight_type_val,
        "batch_size": result.batch_size,
        "eligible_users": result.eligible_users,
        "generated_users": result.generated_users,
        "skipped_users": result.skipped_users,
        "failed_users": result.failed_users,
        "processed": result.processed,
        "duration_seconds": round(result.duration_seconds, 2),
    }
    if run_mode == "manual":
        trigger = _manual_trigger_metadata(result)
        if trigger is not None:
            metadata["trigger"] = trigger
        metadata["stale_users"] = result.stale_users
        metadata["stale_never_generated"] = result.stale_never_generated
        metadata["stale_keywords_updated"] = result.stale_keywords_updated
    return metadata


def _build_activity_description(
    *,
    run_mode: LearningSpotlightRunMode,
    result: DailyGenerationResult,
) -> str:
    cycle_day = result.cycle_day or 0
    cycle_day_str = f"Day {cycle_day}"
    spotlight_type_title = _spotlight_type_title(result)
    return (
        "ran Learning Spotlight daily generation "
        f"({cycle_day_str}: {spotlight_type_title}, {result.processed} processed)"
    )


async def _first_superadmin_user_id(session: AsyncSession) -> UUID | None:
    from sqlmodel import select

    from apps.accounts.db_models import Role, User, UserRole

    stmt = (
        select(User.id)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(Role.name == "superadmin", User.is_deleted.is_(False))
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def _write_spotlight_activity(
    *,
    session_factory,
    run_mode: LearningSpotlightRunMode,
    action: str,
    description: str,
    metadata: dict,
    triggered_by_user_id: UUID | None,
    triggered_by_role: str | None,
) -> None:
    """Persist a Learning Spotlight activity row after generation finishes.

    Manual runs are attributed to the admin who queued them. Scheduled runs
    keep the unprefixed description and use an existing superadmin as the
    required activity-log actor. Logging failures do not fail generation.
    """
    from apps.administration.repositories.admin_activity_log_repository import (
        create_admin_activity_log_record,
    )
    from apps.administration.services.admin_activity_log_service import (
        create_admin_activity_log,
        json_safe,
    )

    try:
        factory = _resolve_session_factory(session_factory)
        async with factory() as session:
            safe_metadata = json_safe(metadata)
            if run_mode == "manual" and triggered_by_user_id is not None:
                await create_admin_activity_log(
                    session,
                    user_id=triggered_by_user_id,
                    role=triggered_by_role or "superadmin",
                    action=action,
                    module="learning_spotlight",
                    record_id=None,
                    description=description,
                    metadata=safe_metadata,
                    commit=True,
                )
                return

            actor_id = await _first_superadmin_user_id(session)
            if actor_id is None:
                logger.warning(
                    "[learning-spotlight-cron] Skipped %s activity log; no superadmin actor",
                    run_mode,
                )
                return
            await create_admin_activity_log_record(
                session,
                user_id=actor_id,
                role="superadmin",
                action=action,
                module="learning_spotlight",
                record_id=None,
                description=description,
                metadata=safe_metadata,
            )
            await session.commit()
    except Exception:
        logger.exception(
            "[learning-spotlight-cron] Failed to write %s activity log run_mode=%s",
            action,
            run_mode,
        )


async def _log_spotlight_generation_activity(
    *,
    session_factory,
    run_mode: LearningSpotlightRunMode,
    business_date: date,
    result: DailyGenerationResult,
    triggered_by_user_id: UUID | None,
    triggered_by_role: str | None,
) -> None:
    await _write_spotlight_activity(
        session_factory=session_factory,
        run_mode=run_mode,
        action="run",
        description=_build_activity_description(run_mode=run_mode, result=result),
        metadata=_build_activity_metadata(
            run_mode=run_mode,
            business_date=business_date,
            result=result,
        ),
        triggered_by_user_id=triggered_by_user_id,
        triggered_by_role=triggered_by_role,
    )


async def _log_spotlight_failure_activity(
    *,
    session_factory,
    run_mode: LearningSpotlightRunMode,
    business_date: date,
    error: str,
    triggered_by_user_id: UUID | None,
    triggered_by_role: str | None,
) -> None:
    description = (
        "failed Learning Spotlight generation"
        if run_mode == "manual"
        else "Learning Spotlight daily generation failed"
    )
    await _write_spotlight_activity(
        session_factory=session_factory,
        run_mode=run_mode,
        action="fail",
        description=description,
        metadata={
            "run_mode": run_mode,
            "business_date": business_date.isoformat(),
            "error": error,
        },
        triggered_by_user_id=triggered_by_user_id,
        triggered_by_role=triggered_by_role,
    )


def _is_postgres(session: AsyncSession) -> bool:
    bind = session.get_bind()
    return bool(bind is not None and bind.dialect.name == "postgresql")


@asynccontextmanager
async def _pinned_lock_session(*, engine: AsyncEngine | None = None):
    """Hold one physical DB connection for the advisory-lock-protected run."""
    resolved = engine
    if resolved is None:
        from core.database.session import engine as default_engine

        resolved = default_engine
    async with resolved.connect() as connection:
        async with AsyncSession(bind=connection, expire_on_commit=False) as session:
            session.info["pinned_connection"] = connection
            yield session


def _resolve_session_factory(session_factory):
    return session_factory or async_session_factory


async def _try_advisory_lock(session: AsyncSession) -> bool:
    if not _is_postgres(session):
        return True
    try:
        result = await session.execute(
            text("SELECT pg_try_advisory_lock(:key)"),
            {"key": _LEARNING_SPOTLIGHT_LOCK_KEY},
        )
        locked = result.scalar()
        return bool(locked)
    except Exception:
        logger.exception("[learning-spotlight-cron] Failed to acquire advisory lock")
        return False


async def _release_advisory_lock(session: AsyncSession) -> None:
    if not _is_postgres(session):
        return
    try:
        await session.execute(
            text("SELECT pg_advisory_unlock(:key)"),
            {"key": _LEARNING_SPOTLIGHT_LOCK_KEY},
        )
        logger.info("[learning-spotlight-cron] Lock released")
    except Exception:
        logger.exception("[learning-spotlight-cron] Failed releasing advisory lock")


async def _persist_is_running(is_running: bool, *, session_factory=None) -> None:
    from apps.recommendations.services.recommendation_settings_service import (
        RecommendationSettingsService,
    )

    factory = _resolve_session_factory(session_factory)
    try:
        async with factory() as session:
            await RecommendationSettingsService().set_is_running(
                session,
                is_running=is_running,
            )
        if is_running:
            logger.info("[learning-spotlight-cron] is_running=true")
        else:
            logger.info("[learning-spotlight-cron] is_running reset")
    except Exception:
        logger.exception(
            "[learning-spotlight-cron] Failed to persist is_running=%s",
            is_running,
        )


async def _is_feature_disabled(*, session_factory=None) -> bool:
    from apps.recommendations.services.recommendation_settings_service import (
        RecommendationSettingsService,
    )

    factory = _resolve_session_factory(session_factory)
    async with factory() as session:
        settings = await RecommendationSettingsService().get_persisted_settings(session)
        return settings is not None and not settings.is_enabled


async def run_learning_spotlight(
    *,
    engine: AsyncEngine | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    today: date | None = None,
    now: datetime | None = None,
    run_mode: LearningSpotlightRunMode = "scheduled",
    triggered_by_user_id: UUID | None = None,
    triggered_by_role: str | None = None,
) -> LearningSpotlightRunOutcome:
    """Shared runner used by Celery Beat and the Superadmin endpoint.

    Lifecycle:
    * disabled → do not lock, do not set is_running
    * lock unavailable → do not set is_running, return locked
    * scheduled + business date already completed → skip (manual does not)
    * generation starts → is_running=true
    * success or failure → is_running=false in ``finally``, lock released
    """
    logger.info(
        "[learning-spotlight-cron] Daily tick started run_mode=%s",
        run_mode,
    )
    factory = _resolve_session_factory(session_factory)
    business_date = today or resolve_spotlight_business_date(now=now)

    if await _is_feature_disabled(session_factory=factory):
        logger.info("[learning-spotlight-cron] Skipped: feature disabled")
        if run_mode == "manual":
            # The admin endpoint claims is_running before queueing. A disabled
            # manual task never enters the generation finally block, so release
            # that claim here.
            await _persist_is_running(False, session_factory=factory)
        return LearningSpotlightRunOutcome(
            status="disabled",
            business_date=business_date,
            run_mode=run_mode,
        )

    async with _pinned_lock_session(engine=engine) as lock_session:
        acquired = await _try_advisory_lock(lock_session)
        if not acquired:
            logger.info(
                "[learning-spotlight-cron] Skipped: another instance holds the lock"
            )
            return LearningSpotlightRunOutcome(
                status="locked",
                business_date=business_date,
                run_mode=run_mode,
            )

        try:
            if run_mode == "scheduled":
                existing = await fetch_completed_daily_run(lock_session, business_date)
                if existing is not None:
                    logger.info(
                        "[learning-spotlight-cron] Skipped: business_date=%s already completed",
                        business_date.isoformat(),
                    )
                    return LearningSpotlightRunOutcome(
                        status="already_completed",
                        business_date=business_date,
                        run_mode=run_mode,
                    )

            await _persist_is_running(True, session_factory=factory)
            result: DailyGenerationResult | None = None
            try:
                logger.info(
                    "[learning-spotlight-cron] Generation started business_date=%s run_mode=%s",
                    business_date.isoformat(),
                    run_mode,
                )
                result = await LearningSpotlightDailyGenerationService.run_daily_generation(
                    today=business_date,
                    session_factory=factory,
                    run_mode=run_mode,
                )
                if run_mode == "scheduled" and result.ran:
                    await record_daily_run_completion(
                        lock_session,
                        business_date=business_date,
                        cycle_day=result.cycle_day,
                        spotlight_type=(
                            result.spotlight_type.value
                            if result.spotlight_type
                            else None
                        ),
                    )
                if result.ran:
                    await _log_spotlight_generation_activity(
                        session_factory=factory,
                        run_mode=run_mode,
                        business_date=business_date,
                        result=result,
                        triggered_by_user_id=triggered_by_user_id,
                        triggered_by_role=triggered_by_role,
                    )
                logger.info(
                    "[learning-spotlight-cron] Daily tick completed ran=%s cycle_day=%s "
                    "generated=%s eligible=%s skipped=%s failed=%s run_mode=%s",
                    result.ran,
                    result.cycle_day,
                    result.generated_users,
                    result.eligible_users,
                    result.skipped_users,
                    result.failed_users,
                    run_mode,
                )
                return LearningSpotlightRunOutcome(
                    status="completed",
                    result=result,
                    business_date=business_date,
                    run_mode=run_mode,
                )
            except Exception as exc:
                logger.exception(
                    "[learning-spotlight-cron] Daily tick failed with unhandled exception"
                )
                await _log_spotlight_failure_activity(
                    session_factory=factory,
                    run_mode=run_mode,
                    business_date=business_date,
                    error=str(exc),
                    triggered_by_user_id=triggered_by_user_id,
                    triggered_by_role=triggered_by_role,
                )
                return LearningSpotlightRunOutcome(
                    status="failed",
                    business_date=business_date,
                    result=result,
                    run_mode=run_mode,
                )
            finally:
                await _persist_is_running(False, session_factory=factory)
        finally:
            await _release_advisory_lock(lock_session)


async def process_learning_spotlight_daily() -> LearningSpotlightRunOutcome:
    """One scheduled tick. Delegates to the shared runner."""
    return await run_learning_spotlight(run_mode="scheduled")
