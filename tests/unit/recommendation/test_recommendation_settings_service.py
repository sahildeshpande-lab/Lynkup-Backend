from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from apps.profiles.db_models import LearningRecommendationSettings
from apps.recommendation.services.recommendation_settings_service import (
    RecommendationSettingsService,
)


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> SimpleNamespace:
        return SimpleNamespace(all=lambda: list(self._rows), first=lambda: self._rows[0] if self._rows else None)


class _FakeSession:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self._rows = list(rows or [])
        self.execute = AsyncMock(side_effect=self._execute)
        self.add = MagicMock()
        self.delete = AsyncMock()
        self.commit = AsyncMock()
        self.refresh = AsyncMock(side_effect=self._refresh)

    async def _execute(self, _stmt: Any) -> _FakeResult:
        return _FakeResult(self._rows)

    async def _refresh(self, obj: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_get_settings_creates_defaults_when_missing() -> None:
    session = _FakeSession(rows=[])
    service = RecommendationSettingsService()
    admin_id = uuid4()

    settings = await service.get_settings(
        session,  # type: ignore[arg-type]
        admin_user_id=admin_id,
        create_if_missing=True,
    )

    assert settings.is_enabled is True
    assert settings.generation_frequency_days == 14
    assert settings.max_recommendations == 10
    assert settings.updated_by == admin_id
    session.add.assert_called_once()
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_get_settings_without_admin_does_not_persist() -> None:
    session = _FakeSession(rows=[])
    service = RecommendationSettingsService()

    settings = await service.get_settings(session)  # type: ignore[arg-type]

    assert settings.is_enabled is True
    assert settings.generation_frequency_days == 14
    session.add.assert_not_called()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_defaults_requires_admin() -> None:
    session = _FakeSession(rows=[])
    service = RecommendationSettingsService()

    with pytest.raises(PermissionError, match="authenticated admin"):
        await service.get_settings(
            session,  # type: ignore[arg-type]
            create_if_missing=True,
        )


@pytest.mark.asyncio
async def test_get_settings_returns_existing_row() -> None:
    existing = LearningRecommendationSettings(
        is_enabled=False,
        generation_frequency_days=7,
        max_recommendations=15,
        updated_by=None,
    )
    session = _FakeSession(rows=[existing])
    service = RecommendationSettingsService()

    settings = await service.get_settings(session)  # type: ignore[arg-type]

    assert settings is existing
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_update_settings_updates_only_provided_fields() -> None:
    existing = LearningRecommendationSettings(
        is_enabled=True,
        generation_frequency_days=14,
        max_recommendations=10,
        updated_by=None,
    )
    session = _FakeSession(rows=[existing])
    service = RecommendationSettingsService()
    admin_id = uuid4()

    updated = await service.update_settings(
        session,  # type: ignore[arg-type]
        admin_user_id=admin_id,
        generation_frequency_days=7,
    )

    assert updated.is_enabled is True
    assert updated.generation_frequency_days == 7
    assert updated.max_recommendations == 10
    assert updated.updated_by == admin_id
    assert isinstance(updated.updated_at, datetime)
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_update_settings_rejects_invalid_frequency() -> None:
    existing = LearningRecommendationSettings(
        is_enabled=True,
        generation_frequency_days=14,
        max_recommendations=10,
        updated_by=None,
    )
    session = _FakeSession(rows=[existing])
    service = RecommendationSettingsService()

    with pytest.raises(ValueError, match="generation_frequency_days"):
        await service.update_settings(
            session,  # type: ignore[arg-type]
            admin_user_id=uuid4(),
            generation_frequency_days=400,
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

    caplog.set_level(logging.INFO)
    should_skip = await service.should_generate_recommendation(
        session=MagicMock(),
        user_id=user_id,
        recommendations_updated_at=now - timedelta(days=6),
        current_utc_date=now.date(),
    )
    assert should_skip is False

    should_generate = await service.should_generate_recommendation(
        session=MagicMock(),
        user_id=user_id,
        recommendations_updated_at=now - timedelta(days=7),
        current_utc_date=now.date(),
    )
    assert should_generate is True
