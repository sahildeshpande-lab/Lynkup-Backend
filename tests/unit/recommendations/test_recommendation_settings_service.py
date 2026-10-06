from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
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

    def scalar_one(self):
        if not self._rows:
            raise AssertionError("Expected one scalar result")
        return self._rows[0]


class _FakeSession:
    def __init__(
        self,
        rows: list | None = None,
        *,
        history_rows: list | None = None,
        profile_rows: list | None = None,
        history_count: int | None = None,
    ) -> None:
        self._rows = list(rows or [])
        self._history_rows = list(history_rows or [])
        self._profile_rows = list(profile_rows or [])
        self._history_count = history_count
        self.execute = AsyncMock(side_effect=self._execute)
        self.add = MagicMock()
        self.commit = AsyncMock()
        self.refresh = AsyncMock()

    def _execute(self, stmt):
        stmt_str = str(stmt)
        if "count" in stmt_str.lower() and "learning_recommendation_settings_logs" in stmt_str:
            count = (
                self._history_count
                if self._history_count is not None
                else len(self._history_rows)
            )
            return _FakeResult([count])
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


@pytest.mark.asyncio
async def test_get_settings_with_history_returns_paginated_history() -> None:
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
    session = _FakeSession(
        rows=[existing],
        history_rows=[
            (newer, "John", "Doe"),
            (older, "Sahil", "Deshpande"),
        ],
        profile_rows=[("John", "Doe")],
        history_count=2,
    )

    data = await RecommendationSettingsService().get_settings_with_history(
        session,  # type: ignore[arg-type]
        admin_user_id=admin_id,
        page=1,
        page_size=1,
    )

    assert data["current_settings"]["updated_by"] == "John Doe"
    assert data["history"]["page"] == 1
    assert data["history"]["pageSize"] == 1
    assert data["history"]["totalItems"] == 2
    assert data["history"]["totalPages"] == 2
    assert len(data["history"]["items"]) == 1
    assert data["history"]["items"][0]["id"] == newer.id


@pytest.mark.asyncio
async def test_enable_sets_cycle_start_date_when_null() -> None:
    """Case 1: first enable initializes cycle_start_date to today."""
    existing = LearningRecommendationSettings(
        is_enabled=False,
        generation_frequency_days=2,
        max_recommendations=10,
        cycle_start_date=None,
        updated_by=None,
    )
    session = _FakeSession(rows=[existing])
    fixed_now = datetime(2026, 8, 23, 15, 30, tzinfo=timezone.utc)

    with patch(
        "apps.recommendations.services.recommendation_settings_service._utc_now",
        return_value=fixed_now,
    ):
        updated = await RecommendationSettingsService().update_settings(
            session,  # type: ignore[arg-type]
            admin_user_id=uuid4(),
            is_enabled=True,
        )

    assert updated.is_enabled is True
    assert updated.cycle_start_date == date(2026, 8, 23)
    assert settings_to_dict(updated)["cycle_start_date"] == date(2026, 8, 23)


@pytest.mark.asyncio
async def test_update_other_fields_keeps_existing_cycle_start_date() -> None:
    """Case 2: already enabled with cycle set — other updates leave it unchanged."""
    anchored = date(2026, 8, 20)
    existing = LearningRecommendationSettings(
        is_enabled=True,
        generation_frequency_days=14,
        max_recommendations=10,
        cycle_start_date=anchored,
        updated_by=None,
    )
    session = _FakeSession(rows=[existing])

    updated = await RecommendationSettingsService().update_settings(
        session,  # type: ignore[arg-type]
        admin_user_id=uuid4(),
        max_recommendations=8,
    )

    assert updated.is_enabled is True
    assert updated.max_recommendations == 8
    assert updated.cycle_start_date == anchored


@pytest.mark.asyncio
async def test_disable_preserves_cycle_start_date() -> None:
    """Case 3: disable does not clear cycle_start_date."""
    anchored = date(2026, 8, 23)
    existing = LearningRecommendationSettings(
        is_enabled=True,
        generation_frequency_days=14,
        max_recommendations=10,
        cycle_start_date=anchored,
        updated_by=None,
    )
    session = _FakeSession(rows=[existing])

    updated = await RecommendationSettingsService().update_settings(
        session,  # type: ignore[arg-type]
        admin_user_id=uuid4(),
        is_enabled=False,
    )

    assert updated.is_enabled is False
    assert updated.cycle_start_date == anchored


@pytest.mark.asyncio
async def test_reenable_preserves_existing_cycle_start_date() -> None:
    """Case 4: re-enable after disable keeps the original cycle_start_date."""
    anchored = date(2026, 8, 23)
    existing = LearningRecommendationSettings(
        is_enabled=False,
        generation_frequency_days=14,
        max_recommendations=10,
        cycle_start_date=anchored,
        updated_by=None,
    )
    session = _FakeSession(rows=[existing])
    fixed_now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)

    with patch(
        "apps.recommendations.services.recommendation_settings_service._utc_now",
        return_value=fixed_now,
    ):
        updated = await RecommendationSettingsService().update_settings(
            session,  # type: ignore[arg-type]
            admin_user_id=uuid4(),
            is_enabled=True,
        )

    assert updated.is_enabled is True
    assert updated.cycle_start_date == anchored
    assert updated.cycle_start_date != date(2026, 9, 1)


@pytest.mark.asyncio
async def test_current_cycle_in_settings_and_get_response() -> None:
    admin_id = uuid4()
    existing = LearningRecommendationSettings(
        is_enabled=True,
        generation_frequency_days=3,
        max_recommendations=1,
        cycle_start_date=date(2026, 8, 25),
        cycle_configuration={
            "cycle": [
                "leading_thinker",
                "country_perspective",
                "influential_research",
                "latest_research",
                "beyond_your_field",
            ]
        },
        learning_spotlight_papers_count=1,
        updated_by=admin_id,
    )
    session = _FakeSession(
        rows=[existing],
        history_rows=[],
        profile_rows=[("Super", "Admin")],
    )

    with patch(
        "apps.learningspotlight.services.cycle_service._as_utc_date",
        return_value=date(2026, 8, 26),
    ):
        data = await RecommendationSettingsService().get_settings_with_history(
            session,  # type: ignore[arg-type]
            admin_user_id=admin_id,
        )

        assert data["current_settings"]["current_cycle"] == "country_perspective"
        assert data["current_settings"]["current_cycle_day"] == 2
        assert data["current_settings"]["is_running"] is False
        assert "current_cycle" not in data
        assert "history" in data


@pytest.mark.asyncio
async def test_try_claim_manual_spotlight_run_sets_flag_when_idle() -> None:
    settings = LearningRecommendationSettings(
        is_enabled=True,
        is_running=False,
        generation_frequency_days=14,
        max_recommendations=10,
    )
    service = RecommendationSettingsService()
    session = AsyncMock()
    with patch.object(
        service,
        "_load_singleton",
        new=AsyncMock(return_value=settings),
    ) as load:
        claimed = await service.try_claim_manual_spotlight_run(session)

    assert claimed is True
    assert settings.is_running is True
    load.assert_awaited_once_with(session, for_update=True)
    session.add.assert_called_once_with(settings)
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_try_claim_manual_spotlight_run_rejects_when_running() -> None:
    settings = LearningRecommendationSettings(
        is_enabled=True,
        is_running=True,
        generation_frequency_days=14,
        max_recommendations=10,
    )
    service = RecommendationSettingsService()
    session = AsyncMock()
    with patch.object(service, "_load_singleton", new=AsyncMock(return_value=settings)):
        claimed = await service.try_claim_manual_spotlight_run(session)

    assert claimed is False
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_try_claim_manual_spotlight_run_without_settings_row() -> None:
    service = RecommendationSettingsService()
    session = AsyncMock()
    with patch.object(service, "_load_singleton", new=AsyncMock(return_value=None)):
        assert await service.try_claim_manual_spotlight_run(session) is True
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_settings_to_dict_includes_is_running():
    settings = LearningRecommendationSettings(
        is_enabled=True,
        is_running=True,
        generation_frequency_days=3,
        max_recommendations=2,
        cycle_start_date=date(2026, 8, 25),
    )
    payload = settings_to_dict(settings)
    assert payload["is_running"] is True


@pytest.mark.asyncio
async def test_settings_to_dict_includes_is_pushnotification_enabled():
    settings = LearningRecommendationSettings(
        is_enabled=True,
        generation_frequency_days=3,
        max_recommendations=2,
        is_pushnotification_enabled=False,
    )
    payload = settings_to_dict(settings)
    assert payload["is_pushnotification_enabled"] is False


@pytest.mark.asyncio
async def test_update_settings_persists_is_pushnotification_enabled():
    admin_id = uuid4()
    existing = LearningRecommendationSettings(
        is_enabled=True,
        generation_frequency_days=3,
        max_recommendations=2,
        is_pushnotification_enabled=True,
        updated_by=admin_id,
    )
    session = _FakeSession(rows=[existing])

    updated = await RecommendationSettingsService().update_settings(
        session,  # type: ignore[arg-type]
        admin_user_id=admin_id,
        is_pushnotification_enabled=False,
    )

    assert updated.is_pushnotification_enabled is False


@pytest.mark.asyncio
async def test_is_push_notification_enabled_defaults_true_without_settings_row():
    session = _FakeSession(rows=[])
    enabled = await RecommendationSettingsService().is_push_notification_enabled(
        session  # type: ignore[arg-type]
    )
    assert enabled is True

