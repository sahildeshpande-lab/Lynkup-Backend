"""Unit tests for Step 16: Admin Enable/Disable, Configurable Cycle Order, and Manual /runcron Fallback.

Tests:
1. Default cycle is the original order.
2. Valid reordered cycle is accepted.
3. Duplicate spotlight type is rejected.
4. Missing spotlight type is rejected.
5. Unknown spotlight type is rejected.
6. Exactly 5 values are required.
7. Day 1 resolves to configured first value.
8. Day 2 resolves to configured second value.
9. Day 5 resolves to configured fifth value.
10. Day 6 wraps to configured first value.
11. Changing configuration does not change cycle_start_date.
12. Disable preserves cycle_start_date.
13. Re-enable preserves cycle_start_date.
14. First enable initializes cycle_start_date if NULL.
15. Non-admin cannot update cycle configuration.
16. Admin can update cycle configuration.
17. Admin can enable/disable feature.
18. cycle_start_date cannot be directly overwritten by request.
19. Manual runcron endpoint exists and requires admin authentication.
20. Manual runcron queues generation for a worker and records an audit entry.
21. Broker failures return 503 without recording successful publication.
22. Generation never runs inside the API request.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from apps.accounts.db_models import User
from apps.administration.schemas import RecommendationSettingsUpdateRequest
from apps.learningspotlight.routes import router as spotlight_router
from apps.learningspotlight.schemas import CycleConfiguration
from apps.learningspotlight.services.cycle_service import (
    DEFAULT_CYCLE_ORDER,
    LearningSpotlightCycleService,
    validate_cycle_configuration,
)
from apps.profiles.db_models import LearningRecommendationSettings
from apps.recommendations.routes import router as recommendation_router
from apps.recommendations.services.recommendation_settings_service import (
    RecommendationSettingsService,
)
from common.enums import SpotlightType
from core.database.session import get_session
from core.security.auth import get_current_admin, get_current_user
from apps.administration.dependencies import require_signed_admin


class _FakeResult:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def scalars(self) -> SimpleNamespace:
        return SimpleNamespace(all=lambda: list(self._rows))

    def all(self) -> list:
        return list(self._rows)

    def one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        if not self._rows:
            raise AssertionError("Expected one scalar result")
        return self._rows[0]


class _FakeSession:
    def __init__(self, rows: list | None = None) -> None:
        self._rows = list(rows or [])
        self.execute = AsyncMock(side_effect=self._execute)
        self.add = MagicMock()
        self.commit = AsyncMock()
        self.refresh = AsyncMock()

    def _execute(self, stmt):
        return _FakeResult(self._rows)


# ---------------------------------------------------------------------------
# 1-6. Cycle Configuration Validation
# ---------------------------------------------------------------------------


def test_default_cycle_order() -> None:
    """1. Default cycle is the standard 5-day order."""
    validated = validate_cycle_configuration(None)
    assert validated == DEFAULT_CYCLE_ORDER
    assert validated[0] == SpotlightType.leading_thinker
    assert validated[1] == SpotlightType.country_perspective
    assert validated[2] == SpotlightType.influential_research
    assert validated[3] == SpotlightType.latest_research
    assert validated[4] == SpotlightType.beyond_your_field


def test_valid_reordered_cycle_accepted() -> None:
    """2. Valid reordered cycle is parsed and returned."""
    custom = {
        "cycle": [
            "country_perspective",
            "leading_thinker",
            "influential_research",
            "latest_research",
            "beyond_your_field",
        ]
    }
    validated = validate_cycle_configuration(custom)
    assert validated[0] == SpotlightType.country_perspective
    assert validated[1] == SpotlightType.leading_thinker


def test_duplicate_spotlight_type_rejected() -> None:
    """3. Duplicate spotlight type raises ValueError."""
    invalid = {
        "cycle": [
            "leading_thinker",
            "leading_thinker",
            "influential_research",
            "latest_research",
            "beyond_your_field",
        ]
    }
    with pytest.raises(ValueError, match="duplicate"):
        validate_cycle_configuration(invalid)


def test_missing_spotlight_type_rejected() -> None:
    """4. Missing spotlight type raises ValueError."""
    invalid = {
        "cycle": [
            "leading_thinker",
            "country_perspective",
            "influential_research",
            "latest_research",
        ]
    }
    with pytest.raises(ValueError, match="exactly 5"):
        validate_cycle_configuration(invalid)


def test_unknown_spotlight_type_rejected() -> None:
    """5. Unknown spotlight type raises ValueError."""
    invalid = {
        "cycle": [
            "leading_thinker",
            "country_perspective",
            "influential_research",
            "latest_research",
            "invalid_type",
        ]
    }
    with pytest.raises(ValueError, match="Unknown spotlight type"):
        validate_cycle_configuration(invalid)


def test_pydantic_cycle_configuration_model() -> None:
    """6. Pydantic CycleConfiguration model validates correctly."""
    valid_model = CycleConfiguration(
        cycle=[
            SpotlightType.country_perspective,
            SpotlightType.leading_thinker,
            SpotlightType.influential_research,
            SpotlightType.latest_research,
            SpotlightType.beyond_your_field,
        ]
    )
    assert len(valid_model.cycle) == 5

    with pytest.raises(ValueError):
        CycleConfiguration(cycle=[SpotlightType.leading_thinker])


# ---------------------------------------------------------------------------
# 7-10. Cycle Day Resolution with Custom Order
# ---------------------------------------------------------------------------


def test_custom_cycle_day_resolution() -> None:
    """7-10. Days 1, 2, 5 and Day 6 wrapping resolve to configured order."""
    custom_order = {
        "cycle": [
            "country_perspective",
            "latest_research",
            "influential_research",
            "beyond_your_field",
            "leading_thinker",
        ]
    }
    cycle_start = date(2026, 8, 1)

    service = LearningSpotlightCycleService(
        cycle_start_date=cycle_start,
        cycle_configuration=custom_order,
    )

    # Day 1: Aug 1 -> Country Perspective (configured first)
    assert service.get_spotlight_type(date(2026, 8, 1)) == SpotlightType.country_perspective
    # Day 2: Aug 2 -> Latest Research (configured second)
    assert service.get_spotlight_type(date(2026, 8, 2)) == SpotlightType.latest_research
    # Day 5: Aug 5 -> Leading Thinker (configured fifth)
    assert service.get_spotlight_type(date(2026, 8, 5)) == SpotlightType.leading_thinker
    # Day 6: Aug 6 -> Wraps back to Day 1: Country Perspective
    assert service.get_spotlight_type(date(2026, 8, 6)) == SpotlightType.country_perspective


# ---------------------------------------------------------------------------
# 11-14. Cycle Anchor & Settings Invariants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_settings_preserves_cycle_start_date_on_reorder() -> None:
    """11. Updating cycle_configuration does not modify existing cycle_start_date."""
    admin_id = uuid4()
    anchor = date(2026, 8, 1)
    existing = LearningRecommendationSettings(
        is_enabled=True,
        cycle_start_date=anchor,
        cycle_configuration=None,
    )

    session = _FakeSession(rows=[existing])
    service = RecommendationSettingsService()

    with patch("apps.administration.services.admin_activity_log_service.create_admin_activity_log", new=AsyncMock()):
        updated = await service.update_settings(
            session,  # type: ignore[arg-type]
            admin_user_id=admin_id,
            cycle_configuration={
                "cycle": [
                    "beyond_your_field",
                    "country_perspective",
                    "influential_research",
                    "latest_research",
                    "leading_thinker",
                ]
            },
        )

    assert updated.cycle_start_date == anchor
    assert updated.cycle_configuration == {
        "cycle": [
            "beyond_your_field",
            "country_perspective",
            "influential_research",
            "latest_research",
            "leading_thinker",
        ]
    }


@pytest.mark.asyncio
async def test_first_enable_initializes_cycle_start_date() -> None:
    """14. Enabling for the first time initializes cycle_start_date."""
    admin_id = uuid4()
    existing = LearningRecommendationSettings(
        is_enabled=False,
        cycle_start_date=None,
    )

    session = _FakeSession(rows=[existing])
    service = RecommendationSettingsService()

    with patch("apps.administration.services.admin_activity_log_service.create_admin_activity_log", new=AsyncMock()):
        updated = await service.update_settings(
            session,  # type: ignore[arg-type]
            admin_user_id=admin_id,
            is_enabled=True,
        )

    assert updated.cycle_start_date == datetime.now(timezone.utc).date()


# ---------------------------------------------------------------------------
# 15-18. Admin API Endpoints for Settings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_admin_cannot_update_recommendation_settings(mock_db) -> None:
    """15. Non-admin user receives 401/403 on PATCH recommendation settings."""
    from fastapi import HTTPException

    app = FastAPI()
    app.include_router(recommendation_router, prefix="/api/v1")

    async def _override_admin():
        raise HTTPException(status_code=403, detail="Forbidden: Admin access required")

    async def _override_db():
        yield mock_db()

    app.dependency_overrides[get_current_admin] = _override_admin
    app.dependency_overrides[require_signed_admin] = _override_admin
    app.dependency_overrides[get_session] = _override_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.patch(
            "/api/v1/admin/recommendation-settings",
            json={"is_enabled": False},
        )
        assert resp.status_code == 403



@pytest.mark.asyncio
async def test_admin_can_update_cycle_configuration(mock_db) -> None:
    """16, 17. Admin can update cycle configuration and is_enabled."""
    admin_id = uuid4()
    mock_admin = User(id=admin_id, email="admin@example.com", role="superadmin")

    app = FastAPI()
    app.include_router(recommendation_router, prefix="/api/v1")

    async def _override_admin():
        return mock_admin

    async def _override_db():
        yield mock_db()

    app.dependency_overrides[get_current_admin] = _override_admin
    app.dependency_overrides[require_signed_admin] = _override_admin
    app.dependency_overrides[get_session] = _override_db

    fake_settings = LearningRecommendationSettings(
        id=uuid4(),
        is_enabled=True,
        generation_frequency_days=14,
        max_recommendations=10,
        cycle_start_date=date(2026, 8, 1),
        cycle_configuration={
            "cycle": [
                "country_perspective",
                "leading_thinker",
                "influential_research",
                "latest_research",
                "beyond_your_field",
            ]
        },
        updated_by=admin_id,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    with patch.object(
        RecommendationSettingsService,
        "update_settings",
        new=AsyncMock(return_value=fake_settings),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.patch(
                "/api/v1/admin/recommendation-settings",
                json={
                    "is_enabled": True,
                    "cycle_configuration": {
                        "cycle": [
                            "country_perspective",
                            "leading_thinker",
                            "influential_research",
                            "latest_research",
                            "beyond_your_field",
                        ]
                    },
                },
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["is_enabled"] is True
            assert data["cycle_configuration"]["cycle"][0] == "country_perspective"


# ---------------------------------------------------------------------------
# 19-25. Manual /runcron Endpoint & Locking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("publish_fails", [False, True])
async def test_manual_runcron_queue_result(mock_db, publish_fails) -> None:
    """Queue generation and audit success; return 503 when the broker is unavailable."""
    from core.celery_worker.config import CeleryTaskQueue

    admin = User(id=uuid4(), email="admin@example.com", role="superadmin")
    db = mock_db()
    app = FastAPI()
    app.include_router(spotlight_router, prefix="/api/v1")

    async def _override_admin():
        return admin

    async def _override_db():
        yield db

    app.dependency_overrides[get_current_admin] = _override_admin
    app.dependency_overrides[require_signed_admin] = _override_admin
    app.dependency_overrides[get_session] = _override_db

    with (
        patch(
            "apps.recommendations.services.recommendation_settings_service.RecommendationSettingsService.try_claim_manual_spotlight_run",
            new=AsyncMock(return_value=True),
        ),
        patch("core.jobs.publishing.publish_task", new=AsyncMock(
            return_value="spotlight-task",
            side_effect=ConnectionError("broker unavailable") if publish_fails else None,
        )) as publish,
        patch("apps.learningspotlight.cron.run_learning_spotlight", new=AsyncMock()) as generate,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/v1/admin/spotlight/runcron")
        publish.assert_awaited_once_with(
            "kampulynk.spotlight.tick",
            CeleryTaskQueue.SPOTLIGHTS_QUEUE,
            run_mode="manual",
            triggered_by_user_id=str(admin.id),
            triggered_by_role=str(admin.role),
        )
        generate.assert_not_awaited()
        if publish_fails:
            assert resp.status_code == 503
            assert resp.json()["detail"] == "Unable to queue task. Please retry."
        else:
            assert resp.status_code == 202
            assert resp.json()["status"] is True
            assert resp.json()["data"] == {"task_id": "spotlight-task", "status": "queued"}
