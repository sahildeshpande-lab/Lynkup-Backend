"""Tests for shared Learning Spotlight runner and is_running persistence."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

from apps.learningspotlight.cron import (
    LearningSpotlightRunOutcome,
    process_learning_spotlight_daily,
    resolve_spotlight_business_date,
    run_learning_spotlight,
)
from apps.learningspotlight.services.daily_generation_service import DailyGenerationResult
from apps.profiles.db_models.learning_recommendation_settings_db_model import (
    LearningRecommendationSettings,
)
from apps.recommendations.services.recommendation_settings_service import (
    RecommendationSettingsService,
    settings_to_dict,
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


@asynccontextmanager
async def _fake_pin(*, engine=None):
    yield AsyncMock()


def _fake_result() -> DailyGenerationResult:
    return DailyGenerationResult(
        ran=True,
        cycle_day=1,
        spotlight_type=SpotlightType.leading_thinker,
        batch_size=50,
        total_selected=10,
        processed=10,
        eligible_users=10,
        generated_users=10,
        skipped_users=0,
        failed_users=0,
    )


def _runner_patches(
    *,
    disabled=False,
    locked=False,
    already_completed=False,
    generation=None,
    persist=None,
):
    gen = generation if generation is not None else AsyncMock(return_value=_fake_result())
    set_running = persist if persist is not None else AsyncMock()
    fetch = AsyncMock(return_value=object() if already_completed else None)
    return (
        patch(
            "apps.learningspotlight.cron._pinned_lock_session",
            _fake_pin,
        ),
        patch(
            "apps.learningspotlight.cron._is_feature_disabled",
            new=AsyncMock(return_value=disabled),
        ),
        patch(
            "apps.learningspotlight.cron._try_advisory_lock",
            new=AsyncMock(return_value=not locked),
        ),
        patch(
            "apps.learningspotlight.cron._release_advisory_lock",
            new=AsyncMock(),
        ),
        patch(
            "apps.learningspotlight.cron.fetch_completed_daily_run",
            new=fetch,
        ),
        patch(
            "apps.learningspotlight.cron.record_daily_run_completion",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "apps.learningspotlight.cron._persist_is_running",
            new=set_running,
        ),
        patch(
            "apps.learningspotlight.cron.LearningSpotlightDailyGenerationService.run_daily_generation",
            new=gen,
        ),
        set_running,
        gen,
    )


def test_learning_recommendation_settings_is_running_defaults_false() -> None:
    settings = LearningRecommendationSettings(is_enabled=True)
    assert settings.is_running is False


def test_settings_to_dict_includes_is_running() -> None:
    settings = LearningRecommendationSettings(is_enabled=True, is_running=True)
    payload = settings_to_dict(settings)
    assert payload["is_running"] is True


def test_resolve_spotlight_business_date_uses_utc() -> None:
    eastern = datetime(2026, 9, 16, 20, 0, tzinfo=ZoneInfo("America/New_York"))
    assert resolve_spotlight_business_date(now=eastern) == date(2026, 9, 17)
    noon_utc = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
    assert resolve_spotlight_business_date(now=noon_utc) == date(2026, 9, 16)
    missed_midnight = datetime(2026, 9, 16, 8, 15, tzinfo=timezone.utc)
    assert resolve_spotlight_business_date(now=missed_midnight) == date(2026, 9, 16)


@pytest.mark.asyncio
async def test_run_learning_spotlight_sets_and_resets_is_running() -> None:
    *patches, set_running, gen = _runner_patches()
    fake_result = gen.return_value
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        outcome = await run_learning_spotlight(today=date(2026, 9, 16))

    assert outcome.status == "completed"
    assert outcome.result == fake_result
    assert outcome.business_date == date(2026, 9, 16)
    assert [call.args[0] for call in set_running.await_args_list] == [True, False]
    gen.assert_awaited_once()
    assert gen.await_args.kwargs["today"] == date(2026, 9, 16)


@pytest.mark.asyncio
async def test_run_learning_spotlight_resets_is_running_on_failure() -> None:
    gen = AsyncMock(side_effect=RuntimeError("generation boom"))
    *patches, set_running, _gen = _runner_patches(generation=gen)
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3] as mock_unlock,
        patches[4],
        patches[5] as mock_record,
        patches[6],
        patches[7],
    ):
        outcome = await run_learning_spotlight()

    assert outcome.status == "failed"
    assert outcome.result is None
    assert [call.args[0] for call in set_running.await_args_list] == [True, False]
    mock_unlock.assert_awaited_once()
    mock_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_learning_spotlight_does_not_set_running_when_locked() -> None:
    *patches, set_running, gen = _runner_patches(locked=True)
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3] as mock_unlock,
        patches[4],
        patches[5],
        patches[6],
        patches[7],
    ):
        outcome = await run_learning_spotlight()

    assert outcome.status == "locked"
    set_running.assert_not_awaited()
    gen.assert_not_called()
    mock_unlock.assert_not_called()


@pytest.mark.asyncio
async def test_run_learning_spotlight_does_not_set_running_when_disabled() -> None:
    *patches, set_running, gen = _runner_patches(disabled=True)
    with (
        patches[0],
        patches[1],
        patches[2] as mock_lock,
        patches[3],
        patches[4],
        patches[5],
        patches[6],
        patches[7],
    ):
        outcome = await run_learning_spotlight()

    assert outcome.status == "disabled"
    set_running.assert_not_awaited()
    mock_lock.assert_not_called()
    gen.assert_not_called()


@pytest.mark.asyncio
async def test_run_learning_spotlight_already_completed_skips_generation() -> None:
    *patches, set_running, gen = _runner_patches(already_completed=True)
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3] as mock_unlock,
        patches[4],
        patches[5] as mock_record,
        patches[6],
        patches[7],
    ):
        outcome = await run_learning_spotlight(today=date(2026, 9, 16))

    assert outcome.status == "already_completed"
    assert outcome.business_date == date(2026, 9, 16)
    set_running.assert_not_awaited()
    gen.assert_not_called()
    mock_record.assert_not_awaited()
    mock_unlock.assert_awaited_once()


@pytest.mark.asyncio
async def test_missed_midnight_recovers_only_latest_eligible_date() -> None:
    *patches, _set_running, gen = _runner_patches()
    missed = datetime(2026, 9, 16, 8, 30, tzinfo=timezone.utc)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        outcome = await run_learning_spotlight(now=missed)

    assert outcome.status == "completed"
    assert outcome.business_date == date(2026, 9, 16)
    gen.assert_awaited_once()
    assert gen.await_args.kwargs["today"] == date(2026, 9, 16)
    assert gen.await_args.kwargs["today"] != date(2026, 9, 15)
    assert gen.await_args.kwargs["today"] != date(2026, 9, 14)


@pytest.mark.asyncio
async def test_process_learning_spotlight_daily_delegates_to_shared_runner() -> None:
    outcome = LearningSpotlightRunOutcome(status="completed", result=_fake_result())
    with patch(
        "apps.learningspotlight.cron.run_learning_spotlight",
        new=AsyncMock(return_value=outcome),
    ) as mock_run:
        returned = await process_learning_spotlight_daily()

    mock_run.assert_awaited_once()
    assert returned is outcome


@pytest.mark.asyncio
async def test_run_learning_spotlight_resets_is_running_on_cancellation() -> None:
    gen = AsyncMock(side_effect=asyncio.CancelledError())
    *patches, set_running, _gen = _runner_patches(generation=gen)
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3] as mock_unlock,
        patches[4],
        patches[5],
        patches[6],
        patches[7],
    ):
        with pytest.raises(asyncio.CancelledError):
            await run_learning_spotlight()

    assert [call.args[0] for call in set_running.await_args_list] == [True, False]
    mock_unlock.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_is_running_persists_on_singleton_row() -> None:
    settings = LearningRecommendationSettings(is_running=False)
    session = AsyncMock()
    session.commit = AsyncMock()

    with patch.object(
        RecommendationSettingsService,
        "_load_singleton",
        new=AsyncMock(return_value=settings),
    ):
        await RecommendationSettingsService().set_is_running(session, is_running=True)

    assert settings.is_running is True
    session.add.assert_called_once_with(settings)
    session.commit.assert_awaited_once()
