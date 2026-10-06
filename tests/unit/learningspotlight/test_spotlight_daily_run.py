from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from apps.learningspotlight.cron import (
    run_learning_spotlight,
    _LEARNING_SPOTLIGHT_LOCK_KEY,
)
from apps.learningspotlight.db_models.learning_spotlight_daily_run_db_model import (
    LearningSpotlightDailyRun,
)
from apps.learningspotlight.services.daily_generation_service import DailyGenerationResult
from apps.learningspotlight.services.daily_run_service import (
    fetch_completed_daily_run,
    record_daily_run_completion,
)
from common.enums import SpotlightType


@pytest.fixture(autouse=True)
def _silence_spotlight_activity_logs():
    with (
        patch(
            "apps.learningspotlight.cron._log_spotlight_generation_activity",
            new=AsyncMock(),
        ),
        patch(
            "apps.learningspotlight.cron._log_spotlight_failure_activity",
            new=AsyncMock(),
        ),
    ):
        yield


_PG_SKIP_REASON: str | None = None


def _postgres_async_url() -> str | None:
    from core.database.config import settings

    url = settings.async_database_url
    if url.startswith("postgresql"):
        return url
    return None


def _fake_result() -> DailyGenerationResult:
    return DailyGenerationResult(
        ran=True,
        cycle_day=1,
        spotlight_type=SpotlightType.leading_thinker,
        generated_users=3,
        processed=3,
        eligible_users=3,
    )


async def _backend_pid(session: AsyncSession) -> int:
    return (await session.execute(text("SELECT pg_backend_pid()"))).scalar_one()


async def _try_lock(session: AsyncSession) -> bool:
    result = await session.execute(
        text("SELECT pg_try_advisory_lock(:key)"),
        {"key": _LEARNING_SPOTLIGHT_LOCK_KEY},
    )
    return bool(result.scalar())


async def _unlock(session: AsyncSession) -> None:
    await session.execute(
        text("SELECT pg_advisory_unlock(:key)"),
        {"key": _LEARNING_SPOTLIGHT_LOCK_KEY},
    )


async def _ensure_daily_run_table(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(LearningSpotlightDailyRun.__table__.create, checkfirst=True)


async def _connect_postgres_engine():
    global _PG_SKIP_REASON
    if _PG_SKIP_REASON:
        pytest.skip(_PG_SKIP_REASON)
    pg_url = _postgres_async_url()
    if pg_url is None:
        _PG_SKIP_REASON = "PostgreSQL not available"
        pytest.skip(_PG_SKIP_REASON)
    from core.database.config import settings as db_settings

    engine = create_async_engine(
        pg_url,
        poolclass=NullPool,
        connect_args=db_settings.async_connect_args,
    )
    try:
        async def _ping():
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
                await conn.commit()

        await asyncio.wait_for(_ping(), timeout=5)
        await asyncio.wait_for(_ensure_daily_run_table(engine), timeout=10)
        return engine
    except Exception as exc:
        await engine.dispose()
        _PG_SKIP_REASON = f"PostgreSQL not available: {exc}"
        pytest.skip(_PG_SKIP_REASON)


async def _delete_run(factory, business_date: date) -> None:
    async with factory() as session:
        await session.execute(
            text("DELETE FROM learning_spotlight_daily_runs WHERE business_date = :d"),
            {"d": business_date},
        )
        await session.commit()


@pytest.mark.asyncio
async def test_sqlite_same_business_date_cannot_complete_twice():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    await _ensure_daily_run_table(engine)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    gen = AsyncMock(return_value=_fake_result())
    today = date(2026, 9, 16)

    try:
        with (
            patch(
                "apps.learningspotlight.cron._is_feature_disabled",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "apps.learningspotlight.cron._persist_is_running",
                new=AsyncMock(),
            ),
            patch(
                "apps.learningspotlight.cron.LearningSpotlightDailyGenerationService.run_daily_generation",
                new=gen,
            ),
        ):
            first = await run_learning_spotlight(
                engine=engine,
                session_factory=factory,
                today=today,
            )
            second = await run_learning_spotlight(
                engine=engine,
                session_factory=factory,
                today=today,
            )

        assert first.status == "completed"
        assert second.status == "already_completed"
        assert gen.await_count == 1
        assert gen.await_args.kwargs["today"] == today

        async with factory() as session:
            stored = await fetch_completed_daily_run(session, today)
            assert stored is not None
            assert stored.business_date == today
            assert stored.status == "completed"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sqlite_unique_constraint_rejects_duplicate_insert():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    await _ensure_daily_run_table(engine)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    today = date(2026, 9, 16)
    try:
        async with factory() as session:
            inserted = await record_daily_run_completion(
                session,
                business_date=today,
                cycle_day=1,
                spotlight_type="leading_thinker",
            )
            assert inserted is True
        async with factory() as session:
            inserted_again = await record_daily_run_completion(
                session,
                business_date=today,
                cycle_day=1,
                spotlight_type="leading_thinker",
            )
            assert inserted_again is False
        async with factory() as session:
            rows = (
                await session.execute(
                    text("SELECT COUNT(*) FROM learning_spotlight_daily_runs")
                )
            ).scalar_one()
            assert rows == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_historical_backlog_is_not_regenerated():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    await _ensure_daily_run_table(engine)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    gen = AsyncMock(return_value=_fake_result())
    try:
        with (
            patch(
                "apps.learningspotlight.cron._is_feature_disabled",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "apps.learningspotlight.cron._persist_is_running",
                new=AsyncMock(),
            ),
            patch(
                "apps.learningspotlight.cron.LearningSpotlightDailyGenerationService.run_daily_generation",
                new=gen,
            ),
        ):
            outcome = await run_learning_spotlight(
                engine=engine,
                session_factory=factory,
                today=date(2026, 9, 16),
            )

        assert outcome.status == "completed"
        gen.assert_awaited_once()
        assert gen.await_args.kwargs["today"] == date(2026, 9, 16)
        called_dates = [call.kwargs["today"] for call in gen.await_args_list]
        assert date(2026, 9, 15) not in called_dates
        assert date(2026, 9, 14) not in called_dates
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_advisory_lock_contention_and_connection_pinning():
    engine = await _connect_postgres_engine()
    pids: list[int] = []
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    from apps.learningspotlight import cron as cron_mod

    original_try_lock = cron_mod._try_advisory_lock
    original_release = cron_mod._release_advisory_lock

    async def record_lock(session):
        pids.append(await _backend_pid(session))
        return await original_try_lock(session)

    async def record_fetch(session, business_date):
        pids.append(await _backend_pid(session))
        return await fetch_completed_daily_run(session, business_date)

    async def record_record(session, **kwargs):
        pids.append(await _backend_pid(session))
        return await record_daily_run_completion(session, **kwargs)

    async def record_unlock(session):
        pids.append(await _backend_pid(session))
        await original_release(session)

    today = date(2099, 1, 1)
    try:
        await _delete_run(factory, today)

        with (
            patch("apps.learningspotlight.cron._try_advisory_lock", record_lock),
            patch("apps.learningspotlight.cron.fetch_completed_daily_run", record_fetch),
            patch("apps.learningspotlight.cron.record_daily_run_completion", record_record),
            patch("apps.learningspotlight.cron._release_advisory_lock", record_unlock),
            patch(
                "apps.learningspotlight.cron._is_feature_disabled",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "apps.learningspotlight.cron._persist_is_running",
                new=AsyncMock(),
            ),
            patch(
                "apps.learningspotlight.cron.LearningSpotlightDailyGenerationService.run_daily_generation",
                new=AsyncMock(return_value=_fake_result()),
            ),
        ):
            outcome = await run_learning_spotlight(
                engine=engine,
                session_factory=factory,
                today=today,
            )

        assert outcome.status == "completed"
        assert len(pids) == 4
        assert pids[0] == pids[1] == pids[2] == pids[3]

        held = asyncio.Event()
        release = asyncio.Event()

        async def holder():
            async with engine.connect() as connection:
                async with AsyncSession(bind=connection, expire_on_commit=False) as session:
                    acquired = await _try_lock(session)
                    assert acquired is True
                    held.set()
                    await release.wait()
                    await _unlock(session)

        async def contender():
            await held.wait()
            with (
                patch(
                    "apps.learningspotlight.cron._is_feature_disabled",
                    new=AsyncMock(return_value=False),
                ),
                patch(
                    "apps.learningspotlight.cron._persist_is_running",
                    new=AsyncMock(),
                ) as persist,
                patch(
                    "apps.learningspotlight.cron.LearningSpotlightDailyGenerationService.run_daily_generation",
                    new=AsyncMock(),
                ) as gen,
            ):
                result = await run_learning_spotlight(
                    engine=engine,
                    session_factory=factory,
                    today=date(2099, 1, 2),
                )
                persist.assert_not_awaited()
                gen.assert_not_called()
            release.set()
            return result

        holder_task = asyncio.create_task(holder())
        contender_outcome = await asyncio.wait_for(contender(), timeout=15)
        await asyncio.wait_for(holder_task, timeout=5)
        assert contender_outcome.status == "locked"
    finally:
        await _delete_run(factory, today)
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_repeated_task_same_business_date_does_not_duplicate():
    engine = await _connect_postgres_engine()
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    today = date(2099, 2, 1)
    gen = AsyncMock(return_value=_fake_result())
    try:
        await _delete_run(factory, today)

        with (
            patch(
                "apps.learningspotlight.cron._is_feature_disabled",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "apps.learningspotlight.cron._persist_is_running",
                new=AsyncMock(),
            ),
            patch(
                "apps.learningspotlight.cron.LearningSpotlightDailyGenerationService.run_daily_generation",
                new=gen,
            ),
        ):
            first = await run_learning_spotlight(
                engine=engine,
                session_factory=factory,
                today=today,
            )
            second = await run_learning_spotlight(
                engine=engine,
                session_factory=factory,
                today=today,
            )

        assert first.status == "completed"
        assert second.status == "already_completed"
        assert gen.await_count == 1
    finally:
        await _delete_run(factory, today)
        await engine.dispose()
