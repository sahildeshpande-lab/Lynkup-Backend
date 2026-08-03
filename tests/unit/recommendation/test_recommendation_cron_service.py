from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from apps.recommendation.services.recommendation_cron_service import (
    RecommendationCronService,
    _ProfileCandidate,
)


def _dt(days_ago: int = 0) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


def test_should_generate_when_never_generated() -> None:
    service = RecommendationCronService()
    decision = service._should_generate(
        recommendations_updated_at=None,
        keywords_updated_at=None,
        generation_frequency_days=14,
    )
    assert decision.should_generate is True


def test_should_skip_when_within_frequency_window() -> None:
    service = RecommendationCronService()
    decision = service._should_generate(
        recommendations_updated_at=_dt(days_ago=3),
        keywords_updated_at=_dt(days_ago=1),
        generation_frequency_days=14,
    )
    assert decision.should_generate is False
    assert decision.reason == "waiting for configured interval"


def test_should_skip_when_keywords_unchanged() -> None:
    service = RecommendationCronService()
    last_rec = _dt(days_ago=20)
    decision = service._should_generate(
        recommendations_updated_at=last_rec,
        keywords_updated_at=last_rec - timedelta(days=1),
        generation_frequency_days=14,
    )
    assert decision.should_generate is False
    assert decision.reason == "keywords unchanged"


def test_should_generate_when_interval_passed_and_keywords_changed() -> None:
    service = RecommendationCronService()
    decision = service._should_generate(
        recommendations_updated_at=_dt(days_ago=20),
        keywords_updated_at=_dt(days_ago=1),
        generation_frequency_days=14,
    )
    assert decision.should_generate is True
    assert decision.reason == "recommendation generated"


@pytest.mark.asyncio
async def test_run_returns_immediately_when_disabled(caplog) -> None:
    settings = SimpleNamespace(
        is_enabled=False,
        generation_frequency_days=14,
        max_recommendations=10,
    )
    settings_service = SimpleNamespace(get_settings=AsyncMock(return_value=settings))
    service = RecommendationCronService(settings_service=settings_service)

    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    caplog.set_level(logging.INFO)
    with patch(
        "apps.recommendation.services.recommendation_cron_service.async_session_factory",
        return_value=session,
    ):
        await service._run_recommendation_generation()

    assert "Recommendation generation is disabled." in caplog.text
    assert "Cron Finished" in caplog.text
    settings_service.get_settings.assert_awaited()


@pytest.mark.asyncio
async def test_classmethod_entry_point_delegates() -> None:
    with patch.object(
        RecommendationCronService,
        "_run_recommendation_generation",
        new_callable=AsyncMock,
    ) as run_mock:
        await RecommendationCronService.run_recommendation_generation()

    run_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_skips_and_generates_per_user(caplog) -> None:
    settings = SimpleNamespace(
        is_enabled=True,
        generation_frequency_days=14,
        max_recommendations=5,
    )
    settings_service = SimpleNamespace(get_settings=AsyncMock(return_value=settings))
    persistence = SimpleNamespace(
        save_learning_recommendations=AsyncMock(),
    )
    service = RecommendationCronService(
        settings_service=settings_service,
        persistence_service=persistence,
    )

    generate_user = uuid4()
    skip_user = uuid4()
    profiles = [
        _ProfileCandidate(
            user_id=generate_user,
            extracted_keywords={"major": ["AI"]},
            keywords_updated_at=None,
            recommendations_updated_at=None,
        ),
        _ProfileCandidate(
            user_id=skip_user,
            extracted_keywords={"major": ["ML"]},
            keywords_updated_at=_dt(1),
            recommendations_updated_at=_dt(3),
        ),
    ]

    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    caplog.set_level(logging.INFO)
    with (
        patch(
            "apps.recommendation.services.recommendation_cron_service.async_session_factory",
            return_value=session,
        ),
        patch.object(service, "_get_profiles", AsyncMock(return_value=profiles)),
        patch.object(
            service,
            "_generate_for_user",
            AsyncMock(),
        ) as generate_mock,
    ):
        await service._run_recommendation_generation()

    generate_mock.assert_awaited_once()
    assert generate_mock.await_args.kwargs["user_id"] == generate_user
    assert generate_mock.await_args.kwargs["max_recommendations"] == 5
    assert "action=Generated" in caplog.text
    assert "waiting for configured interval" in caplog.text
    assert "generated=1" in caplog.text
    assert "skipped=1" in caplog.text


@pytest.mark.asyncio
async def test_generate_for_user_reuses_existing_services() -> None:
    user_id = uuid4()
    persistence = SimpleNamespace(save_learning_recommendations=AsyncMock())
    service = RecommendationCronService(persistence_service=persistence)
    session = MagicMock()

    with (
        patch(
            "apps.recommendation.services.recommendation_cron_service.build_semantic_scholar_query",
            return_value='("ai")',
        ),
        patch(
            "apps.recommendation.services.recommendation_cron_service.search_papers",
            AsyncMock(return_value=({"data": [{"paperId": "1"}], "total": 1}, 200)),
        ) as search_mock,
    ):
        await service._generate_for_user(
            session,
            user_id=user_id,
            extracted_keywords={"major": ["AI"]},
            max_recommendations=7,
        )

    search_mock.assert_awaited_once_with('("ai")', limit=7)
    persistence.save_learning_recommendations.assert_awaited_once()
    saved = persistence.save_learning_recommendations.await_args.kwargs
    assert saved["user_id"] == user_id
    assert saved["recommendation_json"]["query"] == '("ai")'
    assert saved["recommendation_json"]["result"]["total"] == 1


@pytest.mark.asyncio
async def test_one_user_failure_does_not_stop_cron(caplog) -> None:
    settings = SimpleNamespace(
        is_enabled=True,
        generation_frequency_days=14,
        max_recommendations=10,
    )
    settings_service = SimpleNamespace(get_settings=AsyncMock(return_value=settings))
    service = RecommendationCronService(settings_service=settings_service)

    fail_user = uuid4()
    ok_user = uuid4()
    profiles = [
        _ProfileCandidate(
            user_id=fail_user,
            extracted_keywords={"major": ["AI"]},
            keywords_updated_at=None,
            recommendations_updated_at=None,
        ),
        _ProfileCandidate(
            user_id=ok_user,
            extracted_keywords={"major": ["ML"]},
            keywords_updated_at=None,
            recommendations_updated_at=None,
        ),
    ]

    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    async def _generate(_session, **kwargs):
        if kwargs["user_id"] == fail_user:
            raise RuntimeError("boom")

    caplog.set_level(logging.INFO)
    with (
        patch(
            "apps.recommendation.services.recommendation_cron_service.async_session_factory",
            return_value=session,
        ),
        patch.object(service, "_get_profiles", AsyncMock(return_value=profiles)),
        patch.object(service, "_generate_for_user", side_effect=_generate),
    ):
        await service._run_recommendation_generation()

    assert "failed=1" in caplog.text
    assert "generated=1" in caplog.text
    assert "Cron Finished" in caplog.text
