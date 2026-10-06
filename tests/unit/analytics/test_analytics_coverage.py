from __future__ import annotations

import json
from datetime import date
from unittest.mock import AsyncMock, Mock, patch

import pytest

from apps.analytics.config import AnalyticsSettings, settings as analytics_settings
from apps.analytics.repositories import analytics_repository as repo
from apps.analytics.schemas import (
    AnalyticsDashboardData,
    AnalyticsDashboardResponse,
    AnalyticsDefaultDistribution,
    AnalyticsDistributionItem,
    AnalyticsDistributionType,
    AnalyticsOverview,
    AnalyticsPeriod,
    AnalyticsTrendItem,
    AnalyticsTypedDistribution,
)
from apps.analytics.services import activity_log_service as activity_svc
from apps.analytics.services import analytics_service as analytics_svc
from apps.invitations.config import InvitationSettings
from common.enums import UserActivityLogType
from common.timezone_settings import AppTimezoneSettings, app_timezone_settings


def test_analytics_and_invitation_share_timezone_setting():
    assert AnalyticsSettings().timezone == app_timezone_settings.timezone
    assert InvitationSettings().timezone == app_timezone_settings.timezone
    assert isinstance(analytics_settings.timezone, str)
    assert len(analytics_settings.timezone) > 0


def test_app_timezone_settings_reads_analytics_timezone_env(monkeypatch):
    monkeypatch.setenv("ANALYTICS_TIMEZONE", "America/Chicago")
    settings = AppTimezoneSettings()
    assert settings.timezone == "America/Chicago"


def test_analytics_schemas_defaults_and_typed_distribution():
    period = AnalyticsPeriod(days=7, start_date="2026-08-01", end_date="2026-08-07")
    overview = AnalyticsOverview()
    assert overview.total_users == 0
    assert overview.daily_active_users == 0

    item = AnalyticsDistributionItem(name="MIT", count=10, percentage=12.5)
    typed = AnalyticsTypedDistribution(type=AnalyticsDistributionType.university, items=[item])
    default = AnalyticsDefaultDistribution(
        universities=[item],
        countries=[],
        majors=[],
    )
    data = AnalyticsDashboardData(
        period=period,
        overview=overview,
        registration_trend=[AnalyticsTrendItem(date="2026-08-01", count=1)],
        dau_trend=[AnalyticsTrendItem(date="2026-08-01", count=2)],
        distribution=default,
    )
    response = AnalyticsDashboardResponse(
        status=True,
        message="ok",
        data=data,
    )
    assert response.data.distribution.universities[0].name == "MIT"
    assert typed.type == AnalyticsDistributionType.university
    assert AnalyticsDistributionType("country").value == "country"


@pytest.mark.asyncio
async def test_fetch_admin_analytics_dashboard_parses_dict_and_json_string(mock_db):
    db = mock_db()

    class _Result:
        def __init__(self, value):
            self._value = value

        def scalar_one(self):
            return self._value

    payload = {"overview": {"total_users": 1}}
    db.execute = AsyncMock(return_value=_Result(payload))
    assert await repo.fetch_admin_analytics_dashboard(db, days=30, distribution_type=None) == payload

    db.execute = AsyncMock(return_value=_Result(json.dumps(payload)))
    assert await repo.fetch_admin_analytics_dashboard(
        db, days=7, distribution_type="university", timezone="UTC"
    ) == payload

    db.execute = AsyncMock(return_value=_Result(None))
    assert await repo.fetch_admin_analytics_dashboard(db, days=90, distribution_type=None) == {}

    db.execute = AsyncMock(
        return_value=_Result([("overview", {"total_users": 2})])
    )
    mapped = await repo.fetch_admin_analytics_dashboard(db, days=30, distribution_type=None)
    assert mapped["overview"]["total_users"] == 2


@pytest.mark.asyncio
async def test_fetch_admin_analytics_dashboard_uses_settings_timezone(mock_db, monkeypatch):
    db = mock_db()

    class _Result:
        def scalar_one(self):
            return {"ok": True}

    db.execute = AsyncMock(return_value=_Result())
    monkeypatch.setattr(
        repo,
        "analytics_settings",
        type("S", (), {"timezone": "America/Chicago"})(),
    )

    await repo.fetch_admin_analytics_dashboard(db, days=30, distribution_type=None)
    params = db.execute.await_args.args[1]
    assert params["timezone"] == "America/Chicago"
    assert params["days"] == 30
    assert params["distribution_type"] is None


@pytest.mark.asyncio
async def test_analytics_service_maps_malformed_payload_safely(mock_db):
    db = mock_db()
    with patch(
        "apps.analytics.services.analytics_service.fetch_admin_analytics_dashboard",
        AsyncMock(
            return_value={
                "period": {},
                "overview": {},
                "registration_trend": [None, {"date": "2026-08-01", "count": "3"}],
                "dau_trend": ["bad", {"date": "2026-08-01"}],
                "distribution": {
                    "universities": [None, {"name": "MIT", "count": "4", "percentage": "10"}],
                    "countries": [],
                    "majors": [],
                },
            }
        ),
    ):
        response = await analytics_svc.get_admin_analytics_dashboard(db, days=30)

    assert response.status is True
    assert response.data.registration_trend[0].count == 3
    assert response.data.distribution.universities[0].name == "MIT"
    assert response.data.distribution.universities[0].percentage == 10.0


@pytest.mark.asyncio
async def test_analytics_service_default_days_when_none(mock_db):
    db = mock_db()
    with patch(
        "apps.analytics.services.analytics_service.fetch_admin_analytics_dashboard",
        AsyncMock(
            return_value={
                "period": {"days": 30, "start_date": "a", "end_date": "b"},
                "overview": {},
                "registration_trend": [],
                "dau_trend": [],
                "distribution": {"universities": [], "countries": [], "majors": []},
            }
        ),
    ) as fetch:
        response = await analytics_svc.get_admin_analytics_dashboard(db, days=None)

    fetch.assert_awaited_once_with(db, days=30, distribution_type=None)
    assert response.data.period.days == 30


@pytest.mark.asyncio
async def test_add_user_activity_log_best_effort_success(mock_db):
    import uuid

    db = mock_db()
    user_id = uuid.uuid4()
    with patch(
        "apps.analytics.services.activity_log_service.create_user_activity_log",
        AsyncMock(),
    ) as create:
        await activity_svc.add_user_activity_log_best_effort(
            db,
            user_id,
            UserActivityLogType.SIGN_IN,
        )
    create.assert_awaited_once()
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_add_user_activity_log_best_effort_ignores_rollback_errors(mock_db):
    import uuid

    db = mock_db()
    db.rollback = AsyncMock(side_effect=RuntimeError("rollback failed"))
    with patch(
        "apps.analytics.services.activity_log_service.create_user_activity_log",
        AsyncMock(side_effect=RuntimeError("insert failed")),
    ):
        await activity_svc.add_user_activity_log_best_effort(
            db,
            uuid.uuid4(),
            UserActivityLogType.CREATE_POST,
        )
    db.rollback.assert_awaited()
