"""Unit tests for Learning Spotlight cron runner, advisory lock, and lifespan.

Tests:
1. Daily generation service is called on tick.
2. 24-hour interval setting remains the default.
3. Disabled settings produce no generation.
4. Advisory lock prevents multiple replicas from running simultaneously.
5. Lock is released after success.
6. Lock is released after failure.
7. FastAPI lifespan no longer starts an in-process scheduler.
8. Manual spotlight cron endpoint requires admin.
9. GET/read/save/feedback routes remain available.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
import inspect

import pytest
from httpx import ASGITransport, AsyncClient


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

from apps.accounts.db_models import User
from apps.learningspotlight.config import settings as spotlight_settings
from apps.learningspotlight.cron import (
    process_learning_spotlight_daily,
)
from apps.learningspotlight.routes import router as spotlight_router
from apps.learningspotlight.schemas import LearningSpotlight
from apps.learningspotlight.services.daily_generation_service import (
    DailyGenerationResult,
    LearningSpotlightDailyGenerationService,
)
from apps.learningspotlight.services.spotlight_persistence_service import (
    SpotlightPersistenceService,
)
from apps.profiles.db_models.learning_recommendation_settings_db_model import (
    LearningRecommendationSettings,
)
from apps.recommendations.services.recommendation_settings_service import (
    RecommendationSettingsService,
)
from common.enums import SpotlightType
from core.database.session import get_session
from core.lifespan import lifespan
from core.security.auth import get_current_admin, get_current_user
from fastapi import FastAPI


def _fake_session_factory():
    session = AsyncMock()
    cm = AsyncMock()
    cm.__aenter__.return_value = session
    cm.__aexit__.return_value = False
    return cm


@asynccontextmanager
async def _fake_pin(*, engine=None):
    yield AsyncMock()


def test_cron_interval_default_is_24_hours() -> None:
    """Verify default cron interval setting remains 24 hours."""
    assert spotlight_settings.learning_spotlight_cron_interval_hours == 24


@pytest.mark.asyncio
async def test_process_learning_spotlight_daily_invokes_generation() -> None:
    """Single tick acquires lock, runs daily generation service, and releases lock."""
    fake_result = DailyGenerationResult(
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

    with patch(
        "apps.learningspotlight.cron._pinned_lock_session",
        _fake_pin,
    ), patch(
        "apps.learningspotlight.cron.fetch_completed_daily_run",
        new=AsyncMock(return_value=None),
    ), patch(
        "apps.learningspotlight.cron.record_daily_run_completion",
        new=AsyncMock(return_value=True),
    ), patch(
        "apps.learningspotlight.cron._is_feature_disabled",
        new=AsyncMock(return_value=False),
    ), patch(
        "apps.learningspotlight.cron._try_advisory_lock",
        new=AsyncMock(return_value=True),
    ) as mock_lock, patch(
        "apps.learningspotlight.cron._release_advisory_lock",
        new=AsyncMock(),
    ) as mock_unlock, patch(
        "apps.learningspotlight.cron._persist_is_running",
        new=AsyncMock(),
    ), patch.object(
        LearningSpotlightDailyGenerationService,
        "run_daily_generation",
        new=AsyncMock(return_value=fake_result),
    ) as mock_run:
        await process_learning_spotlight_daily()

        mock_lock.assert_called_once()
        mock_run.assert_called_once()
        mock_unlock.assert_called_once()


@pytest.mark.asyncio
async def test_advisory_lock_skips_when_held_by_another_replica() -> None:
    """When advisory lock cannot be acquired, tick skips without running generation."""
    with patch(
        "apps.learningspotlight.cron._pinned_lock_session",
        _fake_pin,
    ), patch(
        "apps.learningspotlight.cron.fetch_completed_daily_run",
        new=AsyncMock(return_value=None),
    ), patch(
        "apps.learningspotlight.cron._is_feature_disabled",
        new=AsyncMock(return_value=False),
    ), patch(
        "apps.learningspotlight.cron._try_advisory_lock",
        new=AsyncMock(return_value=False),
    ) as mock_lock, patch(
        "apps.learningspotlight.cron._release_advisory_lock",
        new=AsyncMock(),
    ) as mock_unlock, patch.object(
        LearningSpotlightDailyGenerationService,
        "run_daily_generation",
        new=AsyncMock(),
    ) as mock_run:
        await process_learning_spotlight_daily()

        mock_lock.assert_called_once()
        mock_run.assert_not_called()
        mock_unlock.assert_not_called()


@pytest.mark.asyncio
async def test_advisory_lock_released_on_generation_failure() -> None:
    """When generation throws an exception, advisory lock is still released in finally block."""
    with patch(
        "apps.learningspotlight.cron._pinned_lock_session",
        _fake_pin,
    ), patch(
        "apps.learningspotlight.cron.fetch_completed_daily_run",
        new=AsyncMock(return_value=None),
    ), patch(
        "apps.learningspotlight.cron.record_daily_run_completion",
        new=AsyncMock(return_value=True),
    ), patch(
        "apps.learningspotlight.cron._is_feature_disabled",
        new=AsyncMock(return_value=False),
    ), patch(
        "apps.learningspotlight.cron._try_advisory_lock",
        new=AsyncMock(return_value=True),
    ), patch(
        "apps.learningspotlight.cron._release_advisory_lock",
        new=AsyncMock(),
    ) as mock_unlock, patch(
        "apps.learningspotlight.cron._persist_is_running",
        new=AsyncMock(),
    ), patch.object(
        LearningSpotlightDailyGenerationService,
        "run_daily_generation",
        new=AsyncMock(side_effect=RuntimeError("Database explosion")),
    ):
        await process_learning_spotlight_daily()
        mock_unlock.assert_called_once()


@pytest.mark.asyncio
async def test_disabled_settings_skips_generation() -> None:
    """When is_enabled is False, generation service skips without error."""
    settings = LearningRecommendationSettings(
        id=uuid4(),
        is_enabled=False,
        cycle_start_date=date(2026, 8, 20),
    )
    settings_svc = MagicMock(spec=RecommendationSettingsService)
    settings_svc.get_persisted_settings = AsyncMock(return_value=settings)

    service = LearningSpotlightDailyGenerationService(settings_service=settings_svc)
    with patch(
        "apps.learningspotlight.services.daily_generation_service.async_session_factory"
    ):
        result = await service._run_daily_generation()

    assert result.ran is False
    assert "disabled" in (result.reason or "")
    assert result.generated_users == 0


@pytest.mark.asyncio
async def test_lifespan_does_not_start_in_process_scheduler() -> None:
    """Celery Beat owns periodic jobs; FastAPI lifespan must not start APScheduler."""
    app = FastAPI()
    source = inspect.getsource(lifespan)
    assert "register_jobs" not in source
    assert "scheduler.start" not in source

    with patch("core.lifespan.db_settings.auto_init_db", False), patch(
        "apps.recommendations.config.settings",
        MagicMock(load_models_on_startup=False),
    ):
        async with lifespan(app):
            assert hasattr(app.state, "recommendation_models")


@pytest.mark.asyncio
async def test_manual_cron_route_requires_admin_and_engagement_routes_work(mock_db) -> None:
    """Confirm POST /api/v1/admin/spotlight/runcron requires admin, while GET/PATCH routes work."""
    user_id = uuid4()
    mock_user = User(id=user_id, email="student@example.com", role="user")

    app = FastAPI()
    app.include_router(spotlight_router, prefix="/api/v1")

    async def _override_user():
        return mock_user

    async def _override_admin():
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Forbidden: Admin access required")

    async def _override_db():
        yield mock_db()

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_current_admin] = _override_admin
    app.dependency_overrides[get_session] = _override_db

    fake_spotlight = LearningSpotlight(
        cycle_day=1,
        spotlight_type=SpotlightType.leading_thinker,
        generated_at=datetime(2026, 8, 24, tzinfo=timezone.utc),
        query='("AI")',
        score=95.0,
        paper={
            "paper_id": "p1",
            "title": "Attention Is All You Need",
            "authors": [{"author_id": "a1", "name": "Vaswani"}],
            "url": "https://example.com/p1",
        },
        engagement={"is_read": False, "is_saved": False, "feedback": None},
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        cron_resp = await client.post("/api/v1/admin/spotlight/runcron")
        assert cron_resp.status_code == 403

        with patch.object(
            SpotlightPersistenceService,
            "get_current_spotlight",
            new=AsyncMock(return_value=fake_spotlight),
        ):
            get_resp = await client.get("/api/v1/learning-spotlight")
            assert get_resp.status_code == 200
            assert get_resp.json()["data"]["cycle_day"] == 1

        read_spotlight = fake_spotlight.model_copy(deep=True)
        read_spotlight.papers[0].engagement.is_read = True
        with patch.object(
            SpotlightPersistenceService,
            "update_read_status",
            new=AsyncMock(return_value=read_spotlight),
        ):
            read_resp = await client.patch(
                "/api/v1/learning-spotlight/read",
                json={"is_read": True},
            )
            assert read_resp.status_code == 200
            assert read_resp.json()["data"]["papers"][0]["engagement"]["is_read"] is True

        saved_spotlight = fake_spotlight.model_copy(deep=True)
        saved_spotlight.papers[0].engagement.is_saved = True
        with patch.object(
            SpotlightPersistenceService,
            "update_save_status",
            new=AsyncMock(return_value=saved_spotlight),
        ):
            save_resp = await client.patch(
                "/api/v1/learning-spotlight/save",
                json={"is_saved": True},
            )
            assert save_resp.status_code == 200
            assert save_resp.json()["data"]["papers"][0]["engagement"]["is_saved"] is True

        feedback_spotlight = fake_spotlight.model_copy(deep=True)
        feedback_spotlight.papers[0].engagement.feedback = True
        with patch.object(
            SpotlightPersistenceService,
            "submit_feedback",
            new=AsyncMock(return_value=feedback_spotlight),
        ):
            fb_resp = await client.patch(
                "/api/v1/learning-spotlight/feedback",
                json={"feedback": True},
            )
            assert fb_resp.status_code == 200
            assert fb_resp.json()["data"]["papers"][0]["engagement"]["feedback"] is True
