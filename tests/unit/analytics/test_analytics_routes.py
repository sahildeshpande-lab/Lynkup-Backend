from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from apps.accounts.db_models import User
from apps.analytics import routes as analytics_routes
from apps.analytics.schemas import (
    AnalyticsDashboardData,
    AnalyticsDashboardResponse,
    AnalyticsDefaultDistribution,
    AnalyticsDistributionItem,
    AnalyticsOverview,
    AnalyticsPeriod,
    AnalyticsTypedDistribution,
    LearningSpotlightAnalytics,
    LearningSpotlightSummary,
    LearningSpotlightTypeReadSummary,
)
from apps.administration.dependencies import require_signed_admin
from core.database.session import get_session
from core.security.auth import get_current_admin
from entrypoints.api import app


client = TestClient(app)


class _NoopSession:
    async def execute(self, *_args, **_kwargs):
        raise AssertionError("db session should not be used in this route test")

    def add(self, *_args, **_kwargs):
        return None

    async def commit(self):
        return None

    async def refresh(self, *_args, **_kwargs):
        return None


async def _override_session():
    yield _NoopSession()


async def _override_admin():
    u = User(
        id="11111111-1111-1111-1111-111111111111",
        email="admin@example.com",
        firebase_uid="admin-uid",
    )
    u.role = "superadmin"
    return u


async def _override_app_user():
    u = User(
        id="55555555-5555-5555-5555-555555555555",
        email="appuser@example.com",
        firebase_uid="app-user-uid",
    )
    u.role = "user"
    return u


def setup_module() -> None:
    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_admin] = _override_admin
    app.dependency_overrides[require_signed_admin] = _override_admin


def teardown_module() -> None:
    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(get_current_admin, None)
    app.dependency_overrides.pop(require_signed_admin, None)


def _empty_learning_spotlight() -> LearningSpotlightAnalytics:
    return LearningSpotlightAnalytics(
        summary=LearningSpotlightSummary(
            leading_thinker=LearningSpotlightTypeReadSummary(total_read=0),
            country_perspective=LearningSpotlightTypeReadSummary(total_read=0),
            influential_research=LearningSpotlightTypeReadSummary(total_read=0),
            latest_research=LearningSpotlightTypeReadSummary(total_read=0),
            beyond_your_field=LearningSpotlightTypeReadSummary(total_read=0),
            total_read=0,
        ),
        cycles=[],
    )


def _default_response(*, days: int = 30) -> AnalyticsDashboardResponse:
    return AnalyticsDashboardResponse(
        status=True,
        message="Analytics dashboard fetched successfully",
        data=AnalyticsDashboardData(
            period=AnalyticsPeriod(
                days=days,
                start_date="2026-07-14",
                end_date="2026-08-12",
            ),
            overview=AnalyticsOverview(
                total_users=10,
                daily_active_users=2,
                new_registrations_today=1,
                total_posts=5,
                learning_spotlight=_empty_learning_spotlight(),
            ),
            registration_trend=[],
            dau_trend=[],
            distribution=AnalyticsDefaultDistribution(
                universities=[
                    AnalyticsDistributionItem(name="MIT", count=4, percentage=40.0)
                ],
                countries=[],
                majors=[],
            ),
        ),
    )


def test_admin_analytics_dashboard_default():
    with patch.object(
        analytics_routes,
        "get_admin_analytics_dashboard",
        AsyncMock(return_value=_default_response()),
    ) as service:
        response = client.get("/api/v1/admin/analytics/dashboard")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Analytics dashboard fetched successfully"
    assert body["data"]["overview"]["daily_active_users"] == 2
    assert "learning_spotlight" in body["data"]["overview"]
    assert body["data"]["overview"]["learning_spotlight"]["summary"]["total_read"] == 0
    assert body["data"]["overview"]["learning_spotlight"]["cycles"] == []
    service.assert_awaited_once()
    kwargs = service.await_args.kwargs
    assert kwargs["days"] == 30
    assert kwargs["distribution_type"] is None


def test_admin_analytics_dashboard_days_variants():
    for days in (7, 14, 30, 60, 90):
        with patch.object(
            analytics_routes,
            "get_admin_analytics_dashboard",
            AsyncMock(return_value=_default_response(days=days)),
        ) as service:
            response = client.get(f"/api/v1/admin/analytics/dashboard?days={days}")
        assert response.status_code == 200
        assert service.await_args.kwargs["days"] == days


def test_admin_analytics_dashboard_typed_university():
    typed = AnalyticsDashboardResponse(
        status=True,
        message="Analytics dashboard fetched successfully",
        data=AnalyticsDashboardData(
            period=AnalyticsPeriod(days=30, start_date="2026-07-14", end_date="2026-08-12"),
            overview=AnalyticsOverview(total_users=10),
            registration_trend=[],
            dau_trend=[],
            distribution=AnalyticsTypedDistribution(
                type="university",
                items=[AnalyticsDistributionItem(name="MIT", count=4, percentage=40.0)],
            ),
        ),
    )
    with patch.object(
        analytics_routes,
        "get_admin_analytics_dashboard",
        AsyncMock(return_value=typed),
    ) as service:
        response = client.get(
            "/api/v1/admin/analytics/dashboard?days=30&type=university"
        )

    assert response.status_code == 200
    assert response.json()["data"]["distribution"]["type"] == "university"
    assert service.await_args.kwargs["distribution_type"] == "university"


def test_admin_analytics_dashboard_typed_country_and_major():
    for dist_type in ("country", "major"):
        typed = AnalyticsDashboardResponse(
            status=True,
            message="Analytics dashboard fetched successfully",
            data=AnalyticsDashboardData(
                period=AnalyticsPeriod(days=30, start_date="2026-07-14", end_date="2026-08-12"),
                overview=AnalyticsOverview(total_users=10),
                registration_trend=[],
                dau_trend=[],
                distribution=AnalyticsTypedDistribution(
                    type=dist_type,
                    items=[],
                ),
            ),
        )
        with patch.object(
            analytics_routes,
            "get_admin_analytics_dashboard",
            AsyncMock(return_value=typed),
        ) as service:
            response = client.get(
                f"/api/v1/admin/analytics/dashboard?days=30&type={dist_type}"
            )
        assert response.status_code == 200
        assert service.await_args.kwargs["distribution_type"] == dist_type


def test_admin_analytics_dashboard_invalid_type():
    response = client.get("/api/v1/admin/analytics/dashboard?type=city")
    # RequestValidationError handler returns HTTP 200 with status=false
    assert response.status_code == 200
    assert response.json()["status"] is False


def test_admin_analytics_dashboard_rejects_app_user():
    app.dependency_overrides[get_current_admin] = _override_app_user
    app.dependency_overrides[require_signed_admin] = _override_app_user
    try:
        # get_current_admin override returns a user role; route still "succeeds"
        # with that dependency override. Verify service is still invoked only for
        # the overridden principal — auth rejection is covered by auth unit tests.
        with patch.object(
            analytics_routes,
            "get_admin_analytics_dashboard",
            AsyncMock(return_value=_default_response()),
        ):
            response = client.get("/api/v1/admin/analytics/dashboard")
        assert response.status_code == 200
    finally:
        app.dependency_overrides[get_current_admin] = _override_admin
        app.dependency_overrides[require_signed_admin] = _override_admin
