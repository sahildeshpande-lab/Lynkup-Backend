from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from apps.profiles.db_models import (
    LearningRecommendationSettings,
    LearningRecommendationSettingsLog,
)
from apps.recommendations.services.recommendation_settings_service import (
    RecommendationSettingsService,
    settings_to_dict,
)


class _FakeResult:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def scalars(self) -> SimpleNamespace:
        return SimpleNamespace(all=lambda: list(self._rows))

    def all(self) -> list:
        return list(self._rows)

    def one_or_none(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(
        self,
        rows: list | None = None,
        *,
        history_rows: list | None = None,
        profile_rows: list | None = None,
    ) -> None:
        self._rows = list(rows or [])
        self._history_rows = list(history_rows or [])
        self._profile_rows = list(profile_rows or [])
        self.execute = AsyncMock(side_effect=self._execute)
        self.add = MagicMock()
        self.commit = AsyncMock()
        self.refresh = AsyncMock()

    def _execute(self, stmt):
        stmt_str = str(stmt)
        if "learning_recommendation_settings_logs" in stmt_str:
            return _FakeResult(self._history_rows)
        if "profiles" in stmt_str:
            return _FakeResult(self._profile_rows)
        return _FakeResult(self._rows)


@pytest.mark.asyncio
async def test_should_generate_requires_admin_enabled_settings() -> None:
    service = RecommendationSettingsService()
    session = _FakeSession(rows=[])

    should = await service.should_generate_recommendation(
        session=session,  # type: ignore[arg-type]
        user_id=uuid4(),
        recommendations_updated_at=None,
    )
    assert should is False


@pytest.mark.asyncio
async def test_is_cron_enabled_requires_persisted_row() -> None:
    service = RecommendationSettingsService()
    session = _FakeSession(rows=[])

    assert await service.is_cron_enabled(session) is False  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_is_cron_enabled_respects_admin_toggle() -> None:
    existing = LearningRecommendationSettings(
        is_enabled=False,
        generation_frequency_days=14,
        max_recommendations=10,
        updated_by=None,
    )
    service = RecommendationSettingsService()
    session = _FakeSession(rows=[existing])

    assert await service.is_cron_enabled(session) is False  # type: ignore[arg-type]

    existing.is_enabled = True
    assert await service.is_cron_enabled(session) is True  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_get_settings_without_admin_uses_in_memory_defaults() -> None:
    settings = await RecommendationSettingsService().get_settings(_FakeSession())
    assert settings.is_enabled is True
    assert settings.generation_frequency_days == 14


@pytest.mark.asyncio
async def test_update_settings_can_stop_recommendation_cron() -> None:
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
        is_enabled=False,
    )
    assert updated.is_enabled is False

    added = [call.args[0] for call in session.add.call_args_list]
    assert any(isinstance(obj, LearningRecommendationSettings) for obj in added)
    history_logs = [
        obj for obj in added if isinstance(obj, LearningRecommendationSettingsLog)
    ]
    assert len(history_logs) == 1
    assert history_logs[0].is_enabled is False
    assert history_logs[0].updated_by == admin_id

    should = await service.should_generate_recommendation(
        session=session,  # type: ignore[arg-type]
        user_id=uuid4(),
        recommendations_updated_at=None,
    )
    assert should is False


@pytest.mark.asyncio
async def test_get_settings_with_history_returns_empty_history() -> None:
    admin_id = uuid4()
    existing = LearningRecommendationSettings(
        is_enabled=True,
        generation_frequency_days=14,
        max_recommendations=10,
        updated_by=admin_id,
    )
    session = _FakeSession(
        rows=[existing],
        history_rows=[],
        profile_rows=[("John", "Doe")],
    )
    data = await RecommendationSettingsService().get_settings_with_history(
        session,  # type: ignore[arg-type]
        admin_user_id=admin_id,
    )

    expected = settings_to_dict(existing)
    expected["updated_by"] = "John Doe"
    assert data["current_settings"] == expected
    assert data["history"] == []


@pytest.mark.asyncio
async def test_get_settings_with_history_returns_change_diffs() -> None:
    admin_id = uuid4()
    existing = LearningRecommendationSettings(
        is_enabled=True,
        generation_frequency_days=7,
        max_recommendations=20,
        updated_by=admin_id,
    )
    older = LearningRecommendationSettingsLog(
        id=uuid4(),
        is_enabled=True,
        generation_frequency_days=1,
        max_recommendations=10,
        updated_by=admin_id,
        created_at=datetime.now(timezone.utc) - timedelta(days=2),
    )
    newer = LearningRecommendationSettingsLog(
        id=uuid4(),
        is_enabled=False,
        generation_frequency_days=7,
        max_recommendations=20,
        updated_by=admin_id,
        created_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    # History query is ordered DESC: newest first.
    session = _FakeSession(
        rows=[existing],
        history_rows=[
            (newer, "John", "Doe"),
            (older, "Sahil", "Deshpande"),
        ],
        profile_rows=[("John", "Doe")],
    )

    data = await RecommendationSettingsService().get_settings_with_history(
        session,  # type: ignore[arg-type]
        admin_user_id=admin_id,
    )

    assert data["current_settings"]["updated_by"] == "John Doe"
    assert len(data["history"]) == 2

    newest = data["history"][0]
    assert newest["id"] == newer.id
    assert newest["updated_by"] == "John Doe"
    assert newest["updated_at"] == newer.created_at
    assert newest["changes"] == [
        {
            "field": "is_enabled",
            "previous_value": True,
            "new_value": False,
        },
        {
            "field": "generation_frequency_days",
            "previous_value": 1,
            "new_value": 7,
        },
        {
            "field": "max_recommendations",
            "previous_value": 10,
            "new_value": 20,
        },
    ]

    oldest = data["history"][1]
    assert oldest["id"] == older.id
    assert oldest["updated_by"] == "Sahil Deshpande"
    assert oldest["updated_at"] == older.created_at
    # Compared against defaults (enabled=True, freq=14, max=10)
    assert oldest["changes"] == [
        {
            "field": "generation_frequency_days",
            "previous_value": 14,
            "new_value": 1,
        },
    ]
