"""Learning Spotlight manual stale-user cron and run_mode behavior."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest


@asynccontextmanager
async def _fake_pin(*, engine=None):
    yield AsyncMock()

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from apps.accounts.db_models import User
from apps.learningspotlight.cron import run_learning_spotlight
from apps.learningspotlight.routes import router as spotlight_router
from apps.learningspotlight.services.daily_generation_service import (
    DailyGenerationResult,
    LearningSpotlightDailyGenerationService,
    _ProfileCandidate,
    _profile_candidate_is_stale,
    profile_stale_for_learning_spotlight_clause,
)
from common.enums import SpotlightType
from core.database.session import get_session
from core.security.auth import get_current_admin


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 30, hour, minute, tzinfo=timezone.utc)


def test_profile_candidate_is_stale_cases() -> None:
    base = _ProfileCandidate(
        user_id=uuid4(),
        extracted_keywords={"interests": ["ai"]},
        learning_spotlight=None,
        learning_spotlight_updated_at=None,
        keywords_updated_at=None,
        recommendations_updated_at=None,
    )
    assert _profile_candidate_is_stale(base) is True

    with_spotlight = _ProfileCandidate(
        user_id=uuid4(),
        extracted_keywords={"interests": ["ai"]},
        learning_spotlight={"version": 2},
        learning_spotlight_updated_at=_dt(10, 30),
        keywords_updated_at=_dt(9, 0),
        recommendations_updated_at=None,
    )
    assert _profile_candidate_is_stale(with_spotlight) is False

    keywords_newer = _ProfileCandidate(
        user_id=uuid4(),
        extracted_keywords={"interests": ["ai"]},
        learning_spotlight={"version": 2},
        learning_spotlight_updated_at=_dt(9, 0),
        keywords_updated_at=_dt(10, 30),
        recommendations_updated_at=None,
    )
    assert _profile_candidate_is_stale(keywords_newer) is True

    same_time = _ProfileCandidate(
        user_id=uuid4(),
        extracted_keywords={"interests": ["ai"]},
        learning_spotlight={"version": 2},
        learning_spotlight_updated_at=_dt(9, 0),
        keywords_updated_at=_dt(9, 0),
        recommendations_updated_at=None,
    )
    assert _profile_candidate_is_stale(same_time) is False


def test_eligibility_manual_regenerates_when_cycle_type_mismatch() -> None:
    service = LearningSpotlightDailyGenerationService()
    today = date(2026, 9, 30)
    remapped_old_type = _ProfileCandidate(
        user_id=uuid4(),
        extracted_keywords={"interests": ["machine learning"]},
        learning_spotlight={
            "version": 2,
            "cycle_day": 2,
            "spotlight_type": "leading_thinker",
            "generated_at": _dt(8).isoformat(),
        },
        learning_spotlight_updated_at=_dt(8),
        keywords_updated_at=_dt(9),
        recommendations_updated_at=None,
    )
    for run_mode in ("scheduled", "manual"):
        decision = service._eligibility_decision(
            remapped_old_type,
            cycle_day=2,
            today=today,
            spotlight_type=SpotlightType.country_perspective,
            run_mode=run_mode,
        )
        assert decision.eligible is True

    current_type_today = _ProfileCandidate(
        user_id=uuid4(),
        extracted_keywords={"interests": ["machine learning"]},
        learning_spotlight={
            "version": 2,
            "cycle_day": 2,
            "spotlight_type": "country_perspective",
            "generated_at": _dt(8).isoformat(),
        },
        learning_spotlight_updated_at=_dt(8),
        keywords_updated_at=_dt(10),
        recommendations_updated_at=None,
    )
    for run_mode in ("scheduled", "manual"):
        stale_regen = service._eligibility_decision(
            current_type_today,
            cycle_day=2,
            today=today,
            spotlight_type=SpotlightType.country_perspective,
            run_mode=run_mode,
        )
        assert stale_regen.eligible is True
        assert stale_regen.reason == "eligible_stale_keywords"

    current_type_fresh_keywords = _ProfileCandidate(
        user_id=uuid4(),
        extracted_keywords={"interests": ["machine learning"]},
        learning_spotlight={
            "version": 2,
            "cycle_day": 2,
            "spotlight_type": "country_perspective",
            "generated_at": _dt(8).isoformat(),
        },
        learning_spotlight_updated_at=_dt(10),
        keywords_updated_at=_dt(9),
        recommendations_updated_at=None,
    )
    still_skip = service._eligibility_decision(
        current_type_fresh_keywords,
        cycle_day=2,
        today=today,
        spotlight_type=SpotlightType.country_perspective,
        run_mode="manual",
    )
    assert still_skip.eligible is False
    assert still_skip.reason == "already generated for today"


def test_eligibility_retries_when_spotlight_updated_at_missing_or_before_today() -> None:
    """Null or pre-today learning_spotlight_updated_at must regenerate on Run Cron."""
    service = LearningSpotlightDailyGenerationService()
    today = date(2026, 9, 30)
    snapshot = {
        "version": 2,
        "cycle_day": 3,
        "spotlight_type": "influential_research",
        "generated_at": _dt(8).isoformat(),
    }

    missing_ts = _ProfileCandidate(
        user_id=uuid4(),
        extracted_keywords={"major": ["Computer Science"]},
        learning_spotlight=snapshot,
        learning_spotlight_updated_at=None,
        keywords_updated_at=None,
        recommendations_updated_at=None,
    )
    missing_decision = service._eligibility_decision(
        missing_ts,
        cycle_day=3,
        today=today,
        spotlight_type=SpotlightType.influential_research,
        run_mode="manual",
    )
    assert missing_decision.eligible is True
    assert missing_decision.reason == "eligible_spotlight_not_updated_today"

    yesterday_ts = _ProfileCandidate(
        user_id=uuid4(),
        extracted_keywords={"major": ["Computer Science"]},
        learning_spotlight=snapshot,
        learning_spotlight_updated_at=datetime(2026, 9, 29, 12, tzinfo=timezone.utc),
        keywords_updated_at=None,
        recommendations_updated_at=None,
    )
    yesterday_decision = service._eligibility_decision(
        yesterday_ts,
        cycle_day=3,
        today=today,
        spotlight_type=SpotlightType.influential_research,
        run_mode="manual",
    )
    assert yesterday_decision.eligible is True
    assert yesterday_decision.reason == "eligible_spotlight_not_updated_today"


def test_eligibility_allows_users_without_minor() -> None:
    """Minor is optional — major (or interests) alone is enough for papers."""
    service = LearningSpotlightDailyGenerationService()
    today = date(2026, 9, 30)
    no_minor = _ProfileCandidate(
        user_id=uuid4(),
        extracted_keywords={
            "major": ["Computer Science"],
            "minor": [],
            "interests": [],
        },
        learning_spotlight=None,
        learning_spotlight_updated_at=None,
        keywords_updated_at=_dt(9),
        recommendations_updated_at=None,
    )
    decision = service._eligibility_decision(
        no_minor,
        cycle_day=3,
        today=today,
        spotlight_type=SpotlightType.influential_research,
        run_mode="manual",
    )
    assert decision.eligible is True
    assert decision.reason == "eligible"


@pytest.mark.asyncio
async def test_manual_disabled_releases_claimed_is_running() -> None:
    set_running = AsyncMock()
    gen = AsyncMock()
    with (
        patch("apps.learningspotlight.cron._is_feature_disabled", new=AsyncMock(return_value=True)),
        patch("apps.learningspotlight.cron._persist_is_running", new=set_running),
        patch(
            "apps.learningspotlight.cron.LearningSpotlightDailyGenerationService.run_daily_generation",
            new=gen,
        ),
    ):
        outcome = await run_learning_spotlight(run_mode="manual")
    assert outcome.status == "disabled"
    gen.assert_not_awaited()
    assert [call.args[0] for call in set_running.await_args_list] == [False]


@pytest.mark.asyncio
async def test_scheduled_disabled_does_not_touch_is_running() -> None:
    set_running = AsyncMock()
    with (
        patch("apps.learningspotlight.cron._is_feature_disabled", new=AsyncMock(return_value=True)),
        patch("apps.learningspotlight.cron._persist_is_running", new=set_running),
    ):
        outcome = await run_learning_spotlight(run_mode="scheduled")
    assert outcome.status == "disabled"
    set_running.assert_not_awaited()


@pytest.mark.asyncio
async def test_scheduled_skips_when_business_date_completed() -> None:
    gen = AsyncMock(return_value=DailyGenerationResult(ran=True))
    with (
        patch("apps.learningspotlight.cron._pinned_lock_session", _fake_pin),
        patch("apps.learningspotlight.cron._is_feature_disabled", new=AsyncMock(return_value=False)),
        patch("apps.learningspotlight.cron._try_advisory_lock", new=AsyncMock(return_value=True)),
        patch("apps.learningspotlight.cron._release_advisory_lock", new=AsyncMock()),
        patch(
            "apps.learningspotlight.cron.fetch_completed_daily_run",
            new=AsyncMock(return_value=object()),
        ),
        patch("apps.learningspotlight.cron._persist_is_running", new=AsyncMock()),
        patch(
            "apps.learningspotlight.cron.LearningSpotlightDailyGenerationService.run_daily_generation",
            new=gen,
        ),
    ):
        outcome = await run_learning_spotlight(
            today=date(2026, 9, 30),
            run_mode="scheduled",
        )
    assert outcome.status == "already_completed"
    gen.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_runs_when_business_date_completed() -> None:
    gen = AsyncMock(
        return_value=DailyGenerationResult(
            ran=True,
            stale_users=2,
            processed=2,
            generated_users=1,
            run_mode="manual",
        )
    )
    log_activity = AsyncMock()
    with (
        patch("apps.learningspotlight.cron._pinned_lock_session", _fake_pin),
        patch("apps.learningspotlight.cron._is_feature_disabled", new=AsyncMock(return_value=False)),
        patch("apps.learningspotlight.cron._try_advisory_lock", new=AsyncMock(return_value=True)),
        patch("apps.learningspotlight.cron._release_advisory_lock", new=AsyncMock()),
        patch(
            "apps.learningspotlight.cron.fetch_completed_daily_run",
            new=AsyncMock(return_value=object()),
        ),
        patch("apps.learningspotlight.cron.record_daily_run_completion", new=AsyncMock()) as record,
        patch("apps.learningspotlight.cron._persist_is_running", new=AsyncMock()) as set_running,
        patch(
            "apps.learningspotlight.cron.LearningSpotlightDailyGenerationService.run_daily_generation",
            new=gen,
        ),
        patch("apps.learningspotlight.cron._log_spotlight_generation_activity", new=log_activity),
    ):
        outcome = await run_learning_spotlight(
            today=date(2026, 9, 30),
            run_mode="manual",
            triggered_by_user_id=uuid4(),
            triggered_by_role="superadmin",
        )
    assert outcome.status == "completed"
    gen.assert_awaited_once()
    assert gen.await_args.kwargs["run_mode"] == "manual"
    record.assert_not_awaited()
    log_activity.assert_awaited_once()
    assert [c.args[0] for c in set_running.await_args_list] == [True, False]


@pytest.mark.asyncio
async def test_scheduled_logs_activity_after_generation() -> None:
    result = DailyGenerationResult(
        ran=True,
        cycle_day=2,
        spotlight_type=SpotlightType.leading_thinker,
        generated_users=5,
        processed=5,
        duration_seconds=12.5,
    )
    gen = AsyncMock(return_value=result)
    log_activity = AsyncMock()
    with (
        patch("apps.learningspotlight.cron._pinned_lock_session", _fake_pin),
        patch("apps.learningspotlight.cron._is_feature_disabled", new=AsyncMock(return_value=False)),
        patch("apps.learningspotlight.cron._try_advisory_lock", new=AsyncMock(return_value=True)),
        patch("apps.learningspotlight.cron._release_advisory_lock", new=AsyncMock()),
        patch("apps.learningspotlight.cron.fetch_completed_daily_run", new=AsyncMock(return_value=None)),
        patch("apps.learningspotlight.cron.record_daily_run_completion", new=AsyncMock()),
        patch("apps.learningspotlight.cron._persist_is_running", new=AsyncMock()),
        patch(
            "apps.learningspotlight.cron.LearningSpotlightDailyGenerationService.run_daily_generation",
            new=gen,
        ),
        patch("apps.learningspotlight.cron._log_spotlight_generation_activity", new=log_activity),
    ):
        await run_learning_spotlight(today=date(2026, 9, 30), run_mode="scheduled")
    log_activity.assert_awaited_once()
    assert log_activity.await_args.kwargs["run_mode"] == "scheduled"


@pytest.mark.asyncio
async def test_runcron_does_not_create_queue_activity_log(mock_db) -> None:
    admin_id = uuid4()
    admin = User(id=admin_id, email="admin@example.com", role="superadmin")
    app = FastAPI()
    app.include_router(spotlight_router, prefix="/api/v1")

    async def _override_admin():
        return admin

    async def _override_db():
        yield mock_db()

    app.dependency_overrides[get_current_admin] = _override_admin
    app.dependency_overrides[get_session] = _override_db

    with (
        patch(
            "apps.recommendations.services.recommendation_settings_service.RecommendationSettingsService.try_claim_manual_spotlight_run",
            new=AsyncMock(return_value=True),
        ),
        patch("core.jobs.publishing.publish_admin_task", new=AsyncMock(return_value="t1")),
        patch(
            "apps.administration.services.admin_activity_log_service.create_admin_activity_log",
            new=AsyncMock(),
        ) as audit,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/v1/admin/spotlight/runcron")
    assert resp.status_code == 202
    audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_runcron_resets_claim_when_publish_fails(mock_db) -> None:
    admin = User(id=uuid4(), email="admin@example.com", role="superadmin")
    app = FastAPI()
    app.include_router(spotlight_router, prefix="/api/v1")
    db = mock_db()

    async def _override_admin():
        return admin

    async def _override_db():
        yield db

    app.dependency_overrides[get_current_admin] = _override_admin
    app.dependency_overrides[get_session] = _override_db

    set_running = AsyncMock()
    from fastapi import HTTPException

    with (
        patch(
            "apps.recommendations.services.recommendation_settings_service.RecommendationSettingsService.try_claim_manual_spotlight_run",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "apps.recommendations.services.recommendation_settings_service.RecommendationSettingsService.set_is_running",
            new=set_running,
        ),
        patch(
            "core.jobs.publishing.publish_admin_task",
            new=AsyncMock(
                side_effect=HTTPException(
                    status_code=503,
                    detail="Unable to queue task. Please retry.",
                )
            ),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/v1/admin/spotlight/runcron")
    assert resp.status_code == 503
    set_running.assert_awaited_once_with(db, is_running=False)


@pytest.mark.asyncio
async def test_runcron_rejects_when_is_running(mock_db) -> None:
    admin = User(id=uuid4(), email="admin@example.com", role="superadmin")
    app = FastAPI()
    app.include_router(spotlight_router, prefix="/api/v1")
    async def _override_admin():
        return admin

    async def _override_db():
        yield mock_db()

    app.dependency_overrides[get_current_admin] = _override_admin
    app.dependency_overrides[get_session] = _override_db

    with (
        patch(
            "apps.recommendations.services.recommendation_settings_service.RecommendationSettingsService.try_claim_manual_spotlight_run",
            new=AsyncMock(return_value=False),
        ),
        patch("core.jobs.publishing.publish_admin_task", new=AsyncMock()) as publish,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/v1/admin/spotlight/runcron")
    assert resp.status_code == 409
    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_run_processes_missing_today_candidates() -> None:
    never_id = uuid4()
    recommended_today_id = uuid4()
    yesterday_id = uuid4()
    never = _ProfileCandidate(
        user_id=never_id,
        extracted_keywords={"interests": ["biology"]},
        learning_spotlight=None,
        learning_spotlight_updated_at=None,
        keywords_updated_at=None,
        recommendations_updated_at=None,
    )
    recommended_today = _ProfileCandidate(
        user_id=recommended_today_id,
        extracted_keywords={"interests": ["chemistry"]},
        learning_spotlight={
            "version": 2,
            "cycle_day": 5,
            "spotlight_type": "beyond_your_field",
            "generated_at": _dt(10).isoformat(),
        },
        learning_spotlight_updated_at=_dt(10),
        keywords_updated_at=_dt(9),
        recommendations_updated_at=None,
    )
    yesterday = _ProfileCandidate(
        user_id=yesterday_id,
        extracted_keywords={"interests": ["physics"]},
        learning_spotlight={"version": 2},
        learning_spotlight_updated_at=datetime(2026, 9, 29, 12, tzinfo=timezone.utc),
        keywords_updated_at=datetime(2026, 9, 29, 12, tzinfo=timezone.utc),
        recommendations_updated_at=None,
    )
    generator = AsyncMock(return_value=True)
    service = LearningSpotlightDailyGenerationService(paper_generator=generator)
    service._get_active_profile_candidates = AsyncMock(  # type: ignore[method-assign]
        return_value=[never, recommended_today, yesterday]
    )
    service._settings_service = AsyncMock()
    service._settings_service.get_persisted_settings = AsyncMock(
        return_value=SimpleNamespace(
            is_enabled=True,
            cycle_start_date=date(2026, 9, 1),
            cycle_configuration=None,
            learning_spotlight_papers_count=1,
        )
    )
    service._count_manual_backfill_users = AsyncMock(return_value=(2, 1, 0))  # type: ignore[method-assign]

    with patch(
        "apps.learningspotlight.services.daily_generation_service.async_session_factory"
    ) as factory:
        session = AsyncMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=session)
        factory.return_value.__aexit__ = AsyncMock(return_value=None)
        result = await service._run_daily_generation(
            today=date(2026, 9, 30),
            run_mode="manual",
        )

    assert result.processed == 3
    assert result.generated_users == 2
    assert result.skipped_users == 1
    assert result.skip_reasons.get("already generated for today") == 1
    assert generator.generate_for_user.await_count == 2
    processed_ids = {
        call.args[1] for call in generator.generate_for_user.await_args_list
    }
    assert processed_ids == {never_id, yesterday_id}
    assert generator.generate_for_user.await_args.kwargs["force_regenerate"] is True


@pytest.mark.asyncio
async def test_manual_run_regenerates_when_keywords_newer_than_today_spotlight() -> None:
    """Run Cron regenerates today's paper after profile/keyword updates."""
    stale_today_id = uuid4()
    stale_today = _ProfileCandidate(
        user_id=stale_today_id,
        extracted_keywords={"major": ["Computer Science"], "minor": []},
        learning_spotlight={
            "version": 2,
            "cycle_day": 5,
            "spotlight_type": "beyond_your_field",
            "generated_at": _dt(8).isoformat(),
        },
        learning_spotlight_updated_at=_dt(8),
        keywords_updated_at=_dt(11),
        recommendations_updated_at=None,
    )
    generator = AsyncMock(return_value=True)
    service = LearningSpotlightDailyGenerationService(paper_generator=generator)
    service._get_active_profile_candidates = AsyncMock(  # type: ignore[method-assign]
        return_value=[stale_today]
    )
    service._settings_service = AsyncMock()
    service._settings_service.get_persisted_settings = AsyncMock(
        return_value=SimpleNamespace(
            is_enabled=True,
            cycle_start_date=date(2026, 9, 1),
            cycle_configuration=None,
            learning_spotlight_papers_count=1,
        )
    )
    service._count_manual_backfill_users = AsyncMock(return_value=(0, 0, 1))  # type: ignore[method-assign]

    with patch(
        "apps.learningspotlight.services.daily_generation_service.async_session_factory"
    ) as factory:
        session = AsyncMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=session)
        factory.return_value.__aexit__ = AsyncMock(return_value=None)
        result = await service._run_daily_generation(
            today=date(2026, 9, 30),
            run_mode="manual",
        )

    assert result.processed == 1
    assert result.generated_users == 1
    assert result.skipped_users == 0
    assert generator.generate_for_user.await_count == 1
    call = generator.generate_for_user.await_args
    assert call.args[1] == stale_today_id
    assert call.kwargs["force_regenerate"] is True


@pytest.mark.asyncio
async def test_manual_run_regenerates_after_admin_cycle_remap() -> None:
    user_id = uuid4()
    remapped_cycle = {
        "cycle": [
            SpotlightType.country_perspective.value,
            SpotlightType.leading_thinker.value,
            SpotlightType.influential_research.value,
            SpotlightType.latest_research.value,
            SpotlightType.beyond_your_field.value,
        ]
    }
    existing = _ProfileCandidate(
        user_id=user_id,
        extracted_keywords={"interests": ["biology"]},
        learning_spotlight={
            "version": 2,
            "cycle_day": 2,
            "spotlight_type": "country_perspective",
            "generated_at": datetime(2026, 9, 2, 8, tzinfo=timezone.utc).isoformat(),
        },
        learning_spotlight_updated_at=datetime(2026, 9, 2, 8, tzinfo=timezone.utc),
        keywords_updated_at=datetime(2026, 9, 2, 7, tzinfo=timezone.utc),
        recommendations_updated_at=None,
    )
    generator = AsyncMock(return_value=True)
    service = LearningSpotlightDailyGenerationService(paper_generator=generator)
    service._get_active_profile_candidates = AsyncMock(  # type: ignore[method-assign]
        return_value=[existing]
    )
    service._settings_service = AsyncMock()
    service._settings_service.get_persisted_settings = AsyncMock(
        return_value=SimpleNamespace(
            is_enabled=True,
            cycle_start_date=date(2026, 9, 1),
            cycle_configuration=remapped_cycle,
            learning_spotlight_papers_count=1,
        )
    )
    service._count_manual_backfill_users = AsyncMock(return_value=(0, 0, 0))  # type: ignore[method-assign]

    with patch(
        "apps.learningspotlight.services.daily_generation_service.async_session_factory"
    ) as factory:
        session = AsyncMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=session)
        factory.return_value.__aexit__ = AsyncMock(return_value=None)
        result = await service._run_daily_generation(
            today=date(2026, 9, 2),
            run_mode="manual",
        )

    assert result.generated_users == 1
    assert generator.generate_for_user.await_args.kwargs["cycle_day"] == 2
    assert generator.generate_for_user.await_args.kwargs["spotlight_type"] == (
        SpotlightType.leading_thinker
    )
    assert generator.generate_for_user.await_args.kwargs["force_regenerate"] is True


def test_same_instant_in_another_offset_is_not_stale() -> None:
    from datetime import timedelta, timezone as tz

    spotlight_at = _dt(9, 30)
    keywords_at = spotlight_at.astimezone(tz(timedelta(hours=5, minutes=30)))
    candidate = _ProfileCandidate(
        user_id=uuid4(),
        extracted_keywords={"interests": ["ai"]},
        learning_spotlight={"version": 2},
        learning_spotlight_updated_at=spotlight_at,
        keywords_updated_at=keywords_at,
        recommendations_updated_at=None,
    )
    assert _profile_candidate_is_stale(candidate) is False


def test_manual_activity_metadata_uses_real_counts_and_trigger() -> None:
    from apps.learningspotlight.cron import (
        _build_activity_description,
        _build_activity_metadata,
        _manual_trigger_metadata,
    )

    keyword_only = DailyGenerationResult(
        ran=True,
        cycle_day=2,
        spotlight_type=SpotlightType.leading_thinker,
        processed=12,
        generated_users=10,
        failed_users=1,
        skipped_users=1,
        duration_seconds=42.184,
        stale_users=12,
        stale_never_generated=0,
        stale_keywords_updated=12,
        run_mode="manual",
    )
    metadata = _build_activity_metadata(
        run_mode="manual",
        business_date=date(2026, 9, 30),
        result=keyword_only,
    )
    assert metadata["trigger"] == "updated_keywords"
    assert metadata["stale_users"] == 12
    assert metadata["batch_size"] == keyword_only.batch_size
    assert metadata["eligible_users"] == keyword_only.eligible_users
    assert metadata["processed"] == 12
    assert metadata["generated_users"] == 10
    assert metadata["failed_users"] == 1
    assert metadata["skipped_users"] == 1
    assert metadata["duration_seconds"] == 42.18
    assert metadata["business_date"] == "2026-09-30"
    assert metadata["cycle_day"] == 2
    assert metadata["spotlight_type"] == "leading_thinker"
    assert _build_activity_description(run_mode="manual", result=keyword_only) == (
        "ran Learning Spotlight daily generation (Day 2: Leading Thinker, 12 processed)"
    )

    mixed = DailyGenerationResult(
        ran=True,
        stale_users=3,
        stale_never_generated=1,
        stale_keywords_updated=2,
    )
    assert _manual_trigger_metadata(mixed) == "updated_keywords_and_never_generated"

    never_only = DailyGenerationResult(
        ran=True,
        stale_users=1,
        stale_never_generated=1,
        stale_keywords_updated=0,
    )
    assert _manual_trigger_metadata(never_only) == "never_generated"

    empty = DailyGenerationResult(ran=True, stale_users=0)
    assert _manual_trigger_metadata(empty) is None
    scheduled = _build_activity_metadata(
        run_mode="scheduled",
        business_date=date(2026, 9, 30),
        result=DailyGenerationResult(
            ran=True,
            cycle_day=2,
            spotlight_type=SpotlightType.leading_thinker,
            processed=165,
            generated_users=154,
            failed_users=3,
            skipped_users=8,
            duration_seconds=163.174,
        ),
    )
    assert "trigger" not in scheduled
    assert "stale_users" not in scheduled
    assert scheduled["batch_size"] == 50
    assert scheduled["generated_users"] == 154
    assert scheduled["duration_seconds"] == 163.17
    assert _build_activity_description(
        run_mode="scheduled",
        result=DailyGenerationResult(
            ran=True,
            cycle_day=2,
            spotlight_type=SpotlightType.leading_thinker,
            processed=165,
            generated_users=154,
        ),
    ) == "ran Learning Spotlight daily generation (Day 2: Leading Thinker, 165 processed)"


@pytest.mark.asyncio
async def test_manual_completion_log_is_written_after_generation() -> None:
    result = DailyGenerationResult(
        ran=True,
        cycle_day=2,
        spotlight_type=SpotlightType.leading_thinker,
        processed=12,
        generated_users=10,
        failed_users=1,
        skipped_users=1,
        duration_seconds=42.18,
        stale_users=12,
        stale_keywords_updated=12,
        run_mode="manual",
    )
    order: list[str] = []

    async def generate(**kwargs):
        order.append("generate")
        return result

    async def log_activity(**kwargs):
        order.append("log")

    with (
        patch("apps.learningspotlight.cron._pinned_lock_session", _fake_pin),
        patch("apps.learningspotlight.cron._is_feature_disabled", new=AsyncMock(return_value=False)),
        patch("apps.learningspotlight.cron._try_advisory_lock", new=AsyncMock(return_value=True)),
        patch("apps.learningspotlight.cron._release_advisory_lock", new=AsyncMock()),
        patch("apps.learningspotlight.cron.record_daily_run_completion", new=AsyncMock()) as record,
        patch("apps.learningspotlight.cron._persist_is_running", new=AsyncMock()) as set_running,
        patch(
            "apps.learningspotlight.cron.LearningSpotlightDailyGenerationService.run_daily_generation",
            new=AsyncMock(side_effect=generate),
        ),
        patch("apps.learningspotlight.cron._log_spotlight_generation_activity", new=AsyncMock(side_effect=log_activity)),
    ):
        outcome = await run_learning_spotlight(
            today=date(2026, 9, 30),
            run_mode="manual",
            triggered_by_user_id=uuid4(),
            triggered_by_role="superadmin",
        )

    assert outcome.status == "completed"
    assert order == ["generate", "log"]
    record.assert_not_awaited()
    assert [call.args[0] for call in set_running.await_args_list] == [True, False]


@pytest.mark.asyncio
async def test_manual_zero_stale_users_completes_without_error() -> None:
    result = DailyGenerationResult(
        ran=True,
        cycle_day=2,
        spotlight_type=SpotlightType.leading_thinker,
        processed=0,
        generated_users=0,
        stale_users=0,
        run_mode="manual",
    )
    log_activity = AsyncMock()
    with (
        patch("apps.learningspotlight.cron._pinned_lock_session", _fake_pin),
        patch("apps.learningspotlight.cron._is_feature_disabled", new=AsyncMock(return_value=False)),
        patch("apps.learningspotlight.cron._try_advisory_lock", new=AsyncMock(return_value=True)),
        patch("apps.learningspotlight.cron._release_advisory_lock", new=AsyncMock()),
        patch("apps.learningspotlight.cron.record_daily_run_completion", new=AsyncMock()) as record,
        patch("apps.learningspotlight.cron._persist_is_running", new=AsyncMock()),
        patch(
            "apps.learningspotlight.cron.LearningSpotlightDailyGenerationService.run_daily_generation",
            new=AsyncMock(return_value=result),
        ),
        patch("apps.learningspotlight.cron._log_spotlight_generation_activity", new=log_activity),
    ):
        outcome = await run_learning_spotlight(today=date(2026, 9, 30), run_mode="manual")

    assert outcome.status == "completed"
    record.assert_not_awaited()
    log_activity.assert_awaited_once()
    assert log_activity.await_args.kwargs["result"].stale_users == 0
    assert log_activity.await_args.kwargs["result"].generated_users == 0


@pytest.mark.asyncio
async def test_failure_resets_is_running_and_writes_failure_log() -> None:
    fail_log = AsyncMock()
    with (
        patch("apps.learningspotlight.cron._pinned_lock_session", _fake_pin),
        patch("apps.learningspotlight.cron._is_feature_disabled", new=AsyncMock(return_value=False)),
        patch("apps.learningspotlight.cron._try_advisory_lock", new=AsyncMock(return_value=True)),
        patch("apps.learningspotlight.cron._release_advisory_lock", new=AsyncMock()),
        patch("apps.learningspotlight.cron._persist_is_running", new=AsyncMock()) as set_running,
        patch(
            "apps.learningspotlight.cron.LearningSpotlightDailyGenerationService.run_daily_generation",
            new=AsyncMock(side_effect=RuntimeError("generation boom")),
        ),
        patch("apps.learningspotlight.cron._log_spotlight_generation_activity", new=AsyncMock()) as success_log,
        patch("apps.learningspotlight.cron._log_spotlight_failure_activity", new=fail_log),
    ):
        outcome = await run_learning_spotlight(
            today=date(2026, 9, 30),
            run_mode="manual",
            triggered_by_user_id=uuid4(),
            triggered_by_role="superadmin",
        )

    assert outcome.status == "failed"
    success_log.assert_not_awaited()
    fail_log.assert_awaited_once()
    assert fail_log.await_args.kwargs["run_mode"] == "manual"
    assert fail_log.await_args.kwargs["error"] == "generation boom"
    assert [call.args[0] for call in set_running.await_args_list] == [True, False]


@pytest.mark.asyncio
async def test_manual_activity_log_uses_admin_actor_and_metadata(mock_db) -> None:
    from apps.learningspotlight.cron import _log_spotlight_generation_activity

    admin_id = uuid4()
    result = DailyGenerationResult(
        ran=True,
        cycle_day=2,
        spotlight_type=SpotlightType.leading_thinker,
        processed=12,
        generated_users=10,
        failed_users=1,
        skipped_users=1,
        duration_seconds=42.18,
        stale_users=12,
        stale_keywords_updated=12,
        run_mode="manual",
    )

    @asynccontextmanager
    async def factory():
        yield mock_db()

    with patch(
        "apps.administration.services.admin_activity_log_service.create_admin_activity_log",
        new=AsyncMock(),
    ) as audit:
        await _log_spotlight_generation_activity(
            session_factory=factory,
            run_mode="manual",
            business_date=date(2026, 9, 30),
            result=result,
            triggered_by_user_id=admin_id,
            triggered_by_role="superadmin",
        )

    audit.assert_awaited_once()
    kwargs = audit.await_args.kwargs
    assert kwargs["user_id"] == admin_id
    assert kwargs["role"] == "superadmin"
    assert kwargs["action"] == "run"
    assert kwargs["module"] == "learning_spotlight"
    assert kwargs["description"] == (
        "ran Learning Spotlight daily generation (Day 2: Leading Thinker, 12 processed)"
    )
    assert kwargs["metadata"]["trigger"] == "updated_keywords"
    assert kwargs["metadata"]["batch_size"] == result.batch_size
    assert kwargs["metadata"]["eligible_users"] == result.eligible_users
    assert kwargs["metadata"]["duration_seconds"] == 42.18
    assert kwargs["metadata"]["generated_users"] == 10


@pytest.mark.asyncio
async def test_scheduled_activity_log_is_unprefixed_and_uses_existing_superadmin() -> None:
    from apps.learningspotlight.cron import _log_spotlight_generation_activity

    actor_id = uuid4()
    session = AsyncMock()

    @asynccontextmanager
    async def factory():
        yield session

    result = DailyGenerationResult(
        ran=True,
        cycle_day=2,
        spotlight_type=SpotlightType.leading_thinker,
        processed=165,
        generated_users=154,
        failed_users=3,
        skipped_users=8,
        duration_seconds=163.17,
    )
    with (
        patch(
            "apps.learningspotlight.cron._first_superadmin_user_id",
            new=AsyncMock(return_value=actor_id),
        ),
        patch(
            "apps.administration.repositories.admin_activity_log_repository.create_admin_activity_log_record",
            new=AsyncMock(),
        ) as record,
    ):
        await _log_spotlight_generation_activity(
            session_factory=factory,
            run_mode="scheduled",
            business_date=date(2026, 9, 30),
            result=result,
            triggered_by_user_id=None,
            triggered_by_role=None,
        )

    record.assert_awaited_once()
    kwargs = record.await_args.kwargs
    assert kwargs["user_id"] == actor_id
    assert kwargs["role"] == "superadmin"
    assert kwargs["action"] == "run"
    assert kwargs["description"] == (
        "ran Learning Spotlight daily generation (Day 2: Leading Thinker, 165 processed)"
    )
    assert kwargs["metadata"]["run_mode"] == "scheduled"
    assert kwargs["metadata"]["batch_size"] == result.batch_size
    assert kwargs["metadata"]["eligible_users"] == result.eligible_users
    assert kwargs["metadata"]["generated_users"] == 154
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_generator_reads_current_profile_keywords(mock_db) -> None:
    from apps.learningspotlight.services.daily_generation_service import (
        DefaultSpotlightPaperGenerator,
    )
    from apps.profiles.db_models.profile_db_model import Profile
    from tests.unit.conftest import FakeScalarResult

    user_id = uuid4()
    profile = Profile(
        user_id=user_id,
        major="Biology",
        minor="Genetics",
        extracted_keywords={"major": ["biology"], "interests": ["genetics"]},
    )
    captured: dict = {}

    class _Strategy:
        async def get_candidates(self, context):
            captured["context"] = context
            return []

    with patch(
        "apps.learningspotlight.services.daily_generation_service.get_spotlight_strategy",
        return_value=_Strategy(),
    ):
        generated = await DefaultSpotlightPaperGenerator().generate_for_user(
            mock_db(FakeScalarResult(profile)),
            user_id,
            spotlight_type=SpotlightType.leading_thinker,
            cycle_day=1,
            today=date(2026, 9, 30),
            force_regenerate=True,
        )

    assert generated is False
    assert captured["context"].major == "Biology"
    assert captured["context"].minor == "Genetics"
    assert captured["context"].extracted_keywords["major"] == ["biology"]
    assert captured["context"].interests == ["genetics"]


def test_stale_sql_clause_compiles() -> None:
    from sqlalchemy.dialects import postgresql

    clause = profile_stale_for_learning_spotlight_clause()
    compiled = str(clause.compile(dialect=postgresql.dialect()))
    assert "learning_spotlight_updated_at" in compiled
    assert "keywords_updated_at" in compiled
