from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from apps.profiles.db_models import LearningRecommendationSettings
from apps.recommendation.services.recommendation_settings_service import (
    RecommendationSettingsService,
)


@pytest.mark.asyncio
async def test_should_generate_returns_false_when_disabled(caplog) -> None:
    service = RecommendationSettingsService()
    settings = LearningRecommendationSettings(
        is_enabled=False,
        generation_frequency_days=14,
        max_recommendations=10,
        updated_by=None,
    )
    service.get_settings = AsyncMock(return_value=settings)  # type: ignore[method-assign]

    caplog.set_level(logging.INFO)
    should = await service.should_generate_recommendation(
        session=MagicMock(),
        user_id=uuid4(),
        recommendations_updated_at=None,
    )

    assert should is False
    assert "[recommendation-cron]" in caplog.text


@pytest.mark.asyncio
async def test_should_generate_true_when_never_generated(caplog) -> None:
    service = RecommendationSettingsService()
    settings = LearningRecommendationSettings(
        is_enabled=True,
        generation_frequency_days=14,
        max_recommendations=10,
        updated_by=None,
    )
    service.get_settings = AsyncMock(return_value=settings)  # type: ignore[method-assign]

    caplog.set_level(logging.INFO)
    should = await service.should_generate_recommendation(
        session=MagicMock(),
        user_id=uuid4(),
        recommendations_updated_at=None,
    )

    assert should is True
    assert "[recommendation-cron]" in caplog.text


@pytest.mark.asyncio
async def test_should_generate_respects_generation_frequency(caplog) -> None:
    service = RecommendationSettingsService()
    settings = LearningRecommendationSettings(
        is_enabled=True,
        generation_frequency_days=7,
        max_recommendations=10,
        updated_by=None,
    )
    service.get_settings = AsyncMock(return_value=settings)  # type: ignore[method-assign]

    user_id = uuid4()
    now = datetime.now(timezone.utc)

    # 6 days since last generation -> skip
    caplog.set_level(logging.INFO)
    should_skip = await service.should_generate_recommendation(
        session=MagicMock(),
        user_id=user_id,
        recommendations_updated_at=now - timedelta(days=6),
        current_utc_date=now.date(),
    )
    assert should_skip is False

    # 7 days since last generation -> generate
    should_generate = await service.should_generate_recommendation(
        session=MagicMock(),
        user_id=user_id,
        recommendations_updated_at=now - timedelta(days=7),
        current_utc_date=now.date(),
    )
    assert should_generate is True

