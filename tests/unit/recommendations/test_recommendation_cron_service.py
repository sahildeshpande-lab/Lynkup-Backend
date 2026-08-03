from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from apps.recommendations.services.recommendation_cron_service import (
    RecommendationCronService,
    _ProfileCandidate,
)


def _dt(days_ago: int = 0) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


@pytest.mark.parametrize(
    ("recommendations_updated_at", "keywords_updated_at", "should_generate", "reason"),
    [
        (None, None, True, "recommendation generated"),
        (_dt(3), _dt(1), False, "waiting for configured interval"),
        (_dt(20), _dt(21), False, "keywords unchanged"),
        (_dt(20), _dt(1), True, "recommendation generated"),
    ],
)
def test_should_generate_decision_matrix(
    recommendations_updated_at,
    keywords_updated_at,
    should_generate,
    reason,
) -> None:
    decision = RecommendationCronService()._should_generate(
        recommendations_updated_at=recommendations_updated_at,
        keywords_updated_at=keywords_updated_at,
        generation_frequency_days=14,
    )
    assert decision.should_generate is should_generate
    assert decision.reason == reason


@pytest.mark.asyncio
async def test_cron_stops_when_disabled(caplog) -> None:
    settings_service = SimpleNamespace(
        is_cron_enabled=AsyncMock(return_value=False),
        get_persisted_settings=AsyncMock(
            return_value=SimpleNamespace(is_enabled=False)
        ),
    )
    service = RecommendationCronService(settings_service=settings_service)
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    caplog.set_level(logging.INFO)
    with (
        patch(
            "apps.recommendations.services.recommendation_cron_service.async_session_factory",
            return_value=session,
        ),
        patch.object(
            service,
            "_get_profiles",
            AsyncMock(side_effect=AssertionError("must stop before profiles")),
        ),
    ):
        ran = await service._run_recommendation_generation()

    assert ran is False
    assert "disabled by admin" in caplog.text
    assert "Cron started" not in caplog.text
    assert "Cron completed" not in caplog.text


@pytest.mark.asyncio
async def test_cron_skips_when_no_admin_settings(caplog) -> None:
    settings_service = SimpleNamespace(
        is_cron_enabled=AsyncMock(return_value=False),
        get_persisted_settings=AsyncMock(return_value=None),
    )
    service = RecommendationCronService(settings_service=settings_service)
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    caplog.set_level(logging.INFO)
    with patch(
        "apps.recommendations.services.recommendation_cron_service.async_session_factory",
        return_value=session,
    ):
        ran = await service._run_recommendation_generation()

    assert ran is False
    assert "not configured by admin" in caplog.text
    assert "Cron started" not in caplog.text


@pytest.mark.asyncio
async def test_cron_generates_only_when_keywords_updated(caplog) -> None:
    settings = SimpleNamespace(
        is_enabled=True,
        generation_frequency_days=14,
        max_recommendations=5,
    )
    settings_service = SimpleNamespace(
        is_cron_enabled=AsyncMock(return_value=True),
        get_persisted_settings=AsyncMock(return_value=settings),
    )
    generate_mock = AsyncMock()
    service = RecommendationCronService(
        settings_service=settings_service,
        generation_service=SimpleNamespace(generate_for_user=generate_mock),
    )
    generate_user = uuid4()
    last_rec = _dt(20)
    profiles = [
        _ProfileCandidate(
            user_id=generate_user,
            extracted_keywords={"content_keywords": {"transformer": 3}},
            keywords_updated_at=_dt(1),
            recommendations_updated_at=last_rec,
        ),
        _ProfileCandidate(
            user_id=uuid4(),
            extracted_keywords={"content_keywords": {"stale": 1}},
            keywords_updated_at=last_rec - timedelta(days=2),
            recommendations_updated_at=last_rec,
        ),
    ]
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    caplog.set_level(logging.INFO)
    with (
        patch(
            "apps.recommendations.services.recommendation_cron_service.async_session_factory",
            return_value=session,
        ),
        patch.object(service, "_get_profiles", AsyncMock(return_value=profiles)),
    ):
        await service._run_recommendation_generation()

    generate_mock.assert_awaited_once()
    assert generate_mock.await_args.args[1] == generate_user
    assert generate_mock.await_args.args[2] == 5
    assert "keywords unchanged" in caplog.text
    assert "generated=1" in caplog.text
    assert "skipped=1" in caplog.text
    assert "Cron completed" in caplog.text
