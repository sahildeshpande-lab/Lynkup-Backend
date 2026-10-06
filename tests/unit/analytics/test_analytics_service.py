from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, Mock, patch

import pytest

from common.enums import UserActivityLogType
from apps.analytics.services import activity_log_service as activity_svc
from apps.analytics.services import analytics_service as analytics_svc
from common.exceptions import ApiError


@pytest.mark.asyncio
async def test_add_user_activity_log_persists_correct_fields(mock_db):
    db = mock_db()
    user_id = uuid.uuid4()

    await activity_svc.add_user_activity_log(
        db,
        user_id,
        UserActivityLogType.CREATE_POST,
        commit=False,
    )

    assert db.add.call_count == 1
    record = db.add.call_args.args[0]
    assert record.user_id == user_id
    assert record.activity_log == UserActivityLogType.CREATE_POST
    db.flush.assert_awaited_once()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_user_activity_log_commits_when_requested(mock_db):
    db = mock_db()
    user_id = uuid.uuid4()

    await activity_svc.add_user_activity_log(
        db,
        user_id,
        UserActivityLogType.LIKE_POST,
        commit=True,
    )

    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_add_user_activity_log_best_effort_swallows_errors(mock_db):
    db = mock_db()
    db.flush = AsyncMock(side_effect=RuntimeError("db down"))

    await activity_svc.add_user_activity_log_best_effort(
        db,
        uuid.uuid4(),
        UserActivityLogType.CREATE_COMMENT,
    )

    db.rollback.assert_awaited()


@pytest.mark.asyncio
async def test_get_admin_analytics_dashboard_default_distribution(mock_db):
    db = mock_db()
    payload = {
        "period": {"days": 30, "start_date": "2026-07-14", "end_date": "2026-08-12"},
        "overview": {
            "total_users": 10,
            "daily_active_users": 2,
            "new_registrations_today": 1,
            "total_posts": 5,
        },
        "registration_trend": [{"date": "2026-07-14", "count": 1}],
        "dau_trend": [{"date": "2026-07-14", "count": 2}],
        "distribution": {
            "universities": [{"name": "MIT", "count": 4, "percentage": 40.0}],
            "countries": [{"name": "USA", "iso_code": "US", "count": 3, "percentage": 30.0}],
            "majors": [{"name": "CS", "count": 2, "percentage": 20.0}],
        },
    }

    with patch(
        "apps.analytics.services.analytics_service.fetch_admin_analytics_dashboard",
        AsyncMock(return_value=payload),
    ) as fetch:
        response = await analytics_svc.get_admin_analytics_dashboard(db, days=30)

    fetch.assert_awaited_once_with(db, days=30, distribution_type=None)
    assert response.status is True
    assert response.message == "Analytics dashboard fetched successfully"
    assert response.data.overview.daily_active_users == 2
    assert response.data.overview.learning_spotlight.summary.total_read == 0
    assert response.data.overview.learning_spotlight.cycles == []
    assert response.data.distribution.universities[0].name == "MIT"
    assert response.data.distribution.countries[0].count == 3
    assert response.data.distribution.countries[0].iso_code == "US"
    assert response.data.distribution.universities[0].iso_code is None
    assert response.data.distribution.majors[0].percentage == 20.0


@pytest.mark.asyncio
@pytest.mark.parametrize("days", [7, 14, 30, 60, 90])
async def test_get_admin_analytics_dashboard_days(mock_db, days):
    db = mock_db()
    with patch(
        "apps.analytics.services.analytics_service.fetch_admin_analytics_dashboard",
        AsyncMock(
            return_value={
                "period": {"days": days, "start_date": "2026-01-01", "end_date": "2026-01-02"},
                "overview": {},
                "registration_trend": [],
                "dau_trend": [],
                "distribution": {"universities": [], "countries": [], "majors": []},
            }
        ),
    ) as fetch:
        response = await analytics_svc.get_admin_analytics_dashboard(db, days=days)

    fetch.assert_awaited_once_with(db, days=days, distribution_type=None)
    assert response.data.period.days == days


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("dist_type", "expected_key"),
    [
        ("university", "university"),
        ("country", "country"),
        ("major", "major"),
    ],
)
async def test_get_admin_analytics_dashboard_typed(mock_db, dist_type, expected_key):
    db = mock_db()
    with patch(
        "apps.analytics.services.analytics_service.fetch_admin_analytics_dashboard",
        AsyncMock(
            return_value={
                "period": {"days": 30, "start_date": "2026-01-01", "end_date": "2026-01-31"},
                "overview": {"total_users": 100},
                "registration_trend": [],
                "dau_trend": [],
                "distribution": {
                    "type": expected_key,
                    "items": [
                        {"name": "A", "count": 10, "percentage": 10.0},
                        {"name": "B", "count": 5, "percentage": 5.0},
                    ],
                },
            }
        ),
    ) as fetch:
        response = await analytics_svc.get_admin_analytics_dashboard(
            db,
            days=30,
            distribution_type=dist_type,
        )

    fetch.assert_awaited_once_with(db, days=30, distribution_type=expected_key)
    assert response.data.distribution.type.value == expected_key
    assert len(response.data.distribution.items) == 2
    assert response.data.distribution.items[0].percentage == 10.0


@pytest.mark.asyncio
@pytest.mark.parametrize("days", [0, -1, -30])
async def test_get_admin_analytics_dashboard_invalid_days(mock_db, days):
    with pytest.raises(ApiError, match="days must be greater than 0"):
        await analytics_svc.get_admin_analytics_dashboard(mock_db(), days=days)


def test_normalize_days_period_window():
    """start_date = end_date - (days - 1) for any positive days value."""
    from datetime import date, timedelta

    end = date(2026, 8, 12)
    for days in (7, 14, 30, 60, 90):
        start = end - timedelta(days=days - 1)
        assert (end - start).days == days - 1
        assert analytics_svc._normalize_days(days) == days
    assert analytics_svc._normalize_days(None) == 30


@pytest.mark.asyncio
async def test_get_admin_analytics_dashboard_invalid_type(mock_db):
    with pytest.raises(ApiError, match="type must be one of"):
        await analytics_svc.get_admin_analytics_dashboard(
            mock_db(),
            days=30,
            distribution_type="city",
        )


@pytest.mark.asyncio
async def test_get_admin_analytics_dashboard_zero_data(mock_db):
    db = mock_db()
    with patch(
        "apps.analytics.services.analytics_service.fetch_admin_analytics_dashboard",
        AsyncMock(
            return_value={
                "period": {"days": 7, "start_date": "2026-08-06", "end_date": "2026-08-12"},
                "overview": {
                    "total_users": 0,
                    "daily_active_users": 0,
                    "new_registrations_today": 0,
                    "total_posts": 0,
                },
                "registration_trend": [
                    {"date": "2026-08-06", "count": 0},
                    {"date": "2026-08-12", "count": 0},
                ],
                "dau_trend": [
                    {"date": "2026-08-06", "count": 0},
                    {"date": "2026-08-12", "count": 0},
                ],
                "distribution": {"universities": [], "countries": [], "majors": []},
            }
        ),
    ):
        response = await analytics_svc.get_admin_analytics_dashboard(db, days=7)

    assert response.data.overview.total_users == 0
    assert response.data.overview.learning_spotlight.summary.total_read == 0
    assert response.data.overview.learning_spotlight.cycles == []
    assert response.data.distribution.universities == []
    assert response.data.distribution.countries == []
    assert response.data.distribution.majors == []


def test_dau_activity_types_exclude_sign_in_and_views():
    from common.enums import DAU_ACTIVITY_TYPES

    values = {item.value for item in DAU_ACTIVITY_TYPES}
    assert "SIGN_IN" not in values
    assert "VIEW_POST" not in values
    assert "VIEW_POST_LIST" not in values
    assert "CREATE_POST" in values
    assert "LIKE_POST" in values
    assert "CREATE_COMMENT" in values


def test_dau_unique_users_example():
    """User A (CREATE_POST + LIKE_POST) and User C (CREATE_COMMENT) => DAU = 2."""
    user_a = uuid.uuid4()
    user_c = uuid.uuid4()
    events = [
        (user_a, UserActivityLogType.CREATE_POST),
        (user_a, UserActivityLogType.LIKE_POST),
        (user_c, UserActivityLogType.CREATE_COMMENT),
    ]
    dau_users = {
        user_id
        for user_id, activity in events
        if activity in {
            UserActivityLogType.CREATE_POST,
            UserActivityLogType.CREATE_COMMENT,
            UserActivityLogType.LIKE_POST,
            UserActivityLogType.LIKE_COMMENT,
            UserActivityLogType.REPOST,
            UserActivityLogType.BOOKMARK_POST,
            UserActivityLogType.SHARE_POST,
            UserActivityLogType.SEND_CONNECTION_REQUEST,
            UserActivityLogType.ACCEPT_CONNECTION_REQUEST,
            UserActivityLogType.UPDATE_PROFILE,
        }
    }
    assert len(dau_users) == 2
