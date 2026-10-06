"""Learning Spotlight analytics mapping tests for the admin dashboard."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from apps.analytics.services import analytics_service as analytics_svc


def _empty_spotlight_payload() -> dict:
    return {
        "summary": {
            "leading_thinker": {"total_read": 0},
            "country_perspective": {"total_read": 0},
            "influential_research": {"total_read": 0},
            "latest_research": {"total_read": 0},
            "beyond_your_field": {"total_read": 0},
            "total_read": 0,
        },
        "cycles": [],
    }


def _full_spotlight_payload() -> dict:
    return {
        "summary": {
            "leading_thinker": {"total_read": 18},
            "country_perspective": {"total_read": 14},
            "influential_research": {"total_read": 21},
            "latest_research": {"total_read": 12},
            "beyond_your_field": {"total_read": 27},
            "total_read": 92,
        },
        "cycles": [
            {
                "cycle_day": 1,
                "spotlight_type": "leading_thinker",
                "papers": [
                    {
                        "rank": 1,
                        "paper_id": "paper-id",
                        "article_name": "Research Paper",
                        "total_users_recommended": 42,
                        "total_read_time_seconds": 3850,
                    },
                    {
                        "rank": 2,
                        "paper_id": "paper-b",
                        "article_name": "Second Paper",
                        "total_users_recommended": 10,
                        "total_read_time_seconds": 900,
                    },
                ],
            },
            {
                "cycle_day": 2,
                "spotlight_type": "country_perspective",
                "papers": [],
            },
            {
                "cycle_day": 3,
                "spotlight_type": "influential_research",
                "papers": [],
            },
            {
                "cycle_day": 4,
                "spotlight_type": "latest_research",
                "papers": [],
            },
            {
                "cycle_day": 5,
                "spotlight_type": "beyond_your_field",
                "papers": [],
            },
        ],
    }


def _dashboard_payload(*, days: int = 30, learning_spotlight: dict | None = None) -> dict:
    return {
        "period": {
            "days": days,
            "start_date": "2026-07-14",
            "end_date": "2026-08-12",
        },
        "overview": {
            "total_users": 145,
            "daily_active_users": 0,
            "new_registrations_today": 0,
            "total_posts": 76,
            "learning_spotlight": learning_spotlight
            if learning_spotlight is not None
            else _full_spotlight_payload(),
        },
        "registration_trend": [{"date": "2026-07-14", "count": 1}],
        "dau_trend": [{"date": "2026-07-14", "count": 2}],
        "distribution": {
            "universities": [{"name": "MIT", "count": 4, "percentage": 40.0}],
            "countries": [
                {"name": "USA", "iso_code": "US", "count": 3, "percentage": 30.0}
            ],
            "majors": [{"name": "CS", "count": 2, "percentage": 20.0}],
        },
    }


@pytest.mark.asyncio
async def test_dashboard_includes_learning_spotlight_in_overview(mock_db):
    db = mock_db()
    with patch(
        "apps.analytics.services.analytics_service.fetch_admin_analytics_dashboard",
        AsyncMock(return_value=_dashboard_payload()),
    ):
        response = await analytics_svc.get_admin_analytics_dashboard(db, days=30)

    spotlight = response.data.overview.learning_spotlight
    assert spotlight is not None
    assert spotlight.summary.leading_thinker.total_read == 18
    assert spotlight.summary.country_perspective.total_read == 14
    assert spotlight.summary.influential_research.total_read == 21
    assert spotlight.summary.latest_research.total_read == 12
    assert spotlight.summary.beyond_your_field.total_read == 27
    assert spotlight.summary.total_read == 92
    assert len(spotlight.cycles) == 5
    assert spotlight.cycles[0].cycle_day == 1
    assert spotlight.cycles[0].spotlight_type == "leading_thinker"
    assert spotlight.cycles[0].papers[0].paper_id == "paper-id"
    assert spotlight.cycles[0].papers[0].article_name == "Research Paper"
    assert spotlight.cycles[0].papers[0].total_users_recommended == 42
    assert spotlight.cycles[0].papers[0].total_read_time_seconds == 3850
    assert spotlight.cycles[1].papers == []


@pytest.mark.asyncio
async def test_dashboard_empty_learning_spotlight_defaults(mock_db):
    db = mock_db()
    with patch(
        "apps.analytics.services.analytics_service.fetch_admin_analytics_dashboard",
        AsyncMock(
            return_value=_dashboard_payload(
                days=7, learning_spotlight=_empty_spotlight_payload()
            )
        ),
    ):
        response = await analytics_svc.get_admin_analytics_dashboard(db, days=7)

    spotlight = response.data.overview.learning_spotlight
    assert spotlight.summary.total_read == 0
    assert spotlight.summary.leading_thinker.total_read == 0
    assert spotlight.summary.country_perspective.total_read == 0
    assert spotlight.summary.influential_research.total_read == 0
    assert spotlight.summary.latest_research.total_read == 0
    assert spotlight.summary.beyond_your_field.total_read == 0
    assert spotlight.cycles == []


@pytest.mark.asyncio
async def test_dashboard_missing_learning_spotlight_uses_empty_defaults(mock_db):
    db = mock_db()
    payload = _dashboard_payload()
    del payload["overview"]["learning_spotlight"]
    with patch(
        "apps.analytics.services.analytics_service.fetch_admin_analytics_dashboard",
        AsyncMock(return_value=payload),
    ):
        response = await analytics_svc.get_admin_analytics_dashboard(db, days=30)

    spotlight = response.data.overview.learning_spotlight
    assert spotlight.summary.total_read == 0
    assert spotlight.cycles == []


@pytest.mark.asyncio
@pytest.mark.parametrize("days", [7, 14, 30, 60, 90])
async def test_dashboard_learning_spotlight_forwards_days(mock_db, days):
    db = mock_db()
    with patch(
        "apps.analytics.services.analytics_service.fetch_admin_analytics_dashboard",
        AsyncMock(
            return_value=_dashboard_payload(
                days=days, learning_spotlight=_empty_spotlight_payload()
            )
        ),
    ) as fetch:
        response = await analytics_svc.get_admin_analytics_dashboard(db, days=days)

    fetch.assert_awaited_once_with(db, days=days, distribution_type=None)
    assert response.data.period.days == days
    assert response.data.overview.learning_spotlight.summary.total_read == 0


@pytest.mark.asyncio
async def test_dashboard_distribution_unchanged_with_learning_spotlight(mock_db):
    db = mock_db()
    with patch(
        "apps.analytics.services.analytics_service.fetch_admin_analytics_dashboard",
        AsyncMock(return_value=_dashboard_payload()),
    ):
        response = await analytics_svc.get_admin_analytics_dashboard(db, days=30)

    assert response.data.distribution.universities[0].name == "MIT"
    assert response.data.distribution.countries[0].iso_code == "US"
    assert response.data.distribution.majors[0].percentage == 20.0
    assert response.data.overview.total_users == 145
    assert response.data.overview.total_posts == 76


def test_map_learning_spotlight_dedupe_semantics_documented_in_mapper():
    """Mapper preserves distinct-user recommendation and cumulative read-time fields."""
    mapped = analytics_svc._map_learning_spotlight(
        {
            "summary": {
                "leading_thinker": {"total_read": 1},
                "country_perspective": {"total_read": 0},
                "influential_research": {"total_read": 0},
                "latest_research": {"total_read": 0},
                "beyond_your_field": {"total_read": 0},
                "total_read": 1,
            },
            "cycles": [
                {
                    "cycle_day": 1,
                    "spotlight_type": "leading_thinker",
                    "papers": [
                        {
                            "rank": 1,
                            "paper_id": "p1",
                            "article_name": "A",
                            # same user recommended twice still surfaces as 1 from SQL
                            "total_users_recommended": 1,
                            # multiple READ interactions accumulate seconds
                            "total_read_time_seconds": 150,
                        }
                    ],
                }
            ],
        }
    )
    paper = mapped.cycles[0].papers[0]
    assert paper.total_users_recommended == 1
    assert paper.total_read_time_seconds == 150
    assert mapped.summary.total_read == 1


def test_map_learning_spotlight_top_10_rank_order_preserved():
    papers = [
        {
            "rank": i,
            "paper_id": f"p{i}",
            "article_name": f"Paper {i}",
            "total_users_recommended": 100 - i,
            "total_read_time_seconds": 1000 - (i * 10),
        }
        for i in range(1, 11)
    ]
    mapped = analytics_svc._map_learning_spotlight(
        {
            "summary": _empty_spotlight_payload()["summary"],
            "cycles": [
                {
                    "cycle_day": 1,
                    "spotlight_type": "leading_thinker",
                    "papers": papers,
                }
            ],
        }
    )
    assert len(mapped.cycles[0].papers) == 10
    assert [p.rank for p in mapped.cycles[0].papers] == list(range(1, 11))
    assert mapped.cycles[0].papers[0].total_read_time_seconds == 990


def _rank_read_papers(papers: list[dict]) -> list[dict]:
    """Mirror SQL Top-10 ranking: read-time first, exclude unread papers."""
    readable = [p for p in papers if int(p.get("total_read_time_seconds") or 0) > 0]
    readable.sort(
        key=lambda p: (
            -int(p.get("total_read_time_seconds") or 0),
            -int(p.get("total_users_recommended") or 0),
            str(p.get("paper_id") or ""),
        )
    )
    ranked = []
    for idx, paper in enumerate(readable[:10], start=1):
        ranked.append({**paper, "rank": idx})
    return ranked


def test_top10_excludes_zero_read_papers():
    ranked = _rank_read_papers(
        [
            {
                "paper_id": "A",
                "total_users_recommended": 17,
                "total_read_time_seconds": 0,
            },
            {
                "paper_id": "B",
                "total_users_recommended": 8,
                "total_read_time_seconds": 900,
            },
            {
                "paper_id": "C",
                "total_users_recommended": 15,
                "total_read_time_seconds": 700,
            },
        ]
    )
    assert [p["paper_id"] for p in ranked] == ["B", "C"]
    assert ranked[0]["rank"] == 1
    assert ranked[1]["rank"] == 2


def test_top10_tie_breaker_uses_users_recommended():
    ranked = _rank_read_papers(
        [
            {
                "paper_id": "A",
                "total_users_recommended": 10,
                "total_read_time_seconds": 500,
            },
            {
                "paper_id": "B",
                "total_users_recommended": 20,
                "total_read_time_seconds": 500,
            },
        ]
    )
    assert [p["paper_id"] for p in ranked] == ["B", "A"]


def test_top10_limits_to_ten_and_keeps_fewer():
    many = [
        {
            "paper_id": f"p{i:02d}",
            "total_users_recommended": i,
            "total_read_time_seconds": i * 10,
        }
        for i in range(1, 15)
    ]
    assert len(_rank_read_papers(many)) == 10
    assert _rank_read_papers(many)[0]["paper_id"] == "p14"

    few = [
        {
            "paper_id": "only",
            "total_users_recommended": 2,
            "total_read_time_seconds": 350,
        }
    ]
    ranked_few = _rank_read_papers(few)
    assert len(ranked_few) == 1
    assert ranked_few[0]["total_users_recommended"] == 2
    assert ranked_few[0]["total_read_time_seconds"] == 350

    assert _rank_read_papers(
        [{"paper_id": "none", "total_users_recommended": 5, "total_read_time_seconds": 0}]
    ) == []


def test_sql_procedure_contains_learning_spotlight_requirements():
    sql_path = (
        Path(__file__).resolve().parents[3]
        / "database"
        / "procedures"
        / "get_admin_analytics_dashboard.sql"
    )
    sql = sql_path.read_text(encoding="utf-8")

    assert "v_learning_spotlight JSONB" in sql
    assert re.search(
        r"'learning_spotlight'\s*,\s*v_learning_spotlight",
        sql,
    ), "dashboard payload must include learning_spotlight from v_learning_spotlight"
    assert "learning_recommendation_logs" in sql
    assert "profiles" in sql
    assert re.search(
        r"DISTINCT ON\s*\(\s*user_id\s*,\s*generated_at\s*\)",
        sql,
    )
    assert "learning_paper_interactions" in sql
    assert "i.action = 'READ'" in sql
    assert "i.read_time_seconds > 0" in sql
    assert re.search(r"COUNT\s*\(\s*DISTINCT\s+.*user_id", sql)
    assert re.search(r"ROW_NUMBER\s*\(\s*\)\s*OVER\s*\(", sql)
    assert re.search(r"PARTITION BY\s+cycle_day", sql)
    assert "rank <= 10" in sql
    assert "read_papers AS" in sql
    assert "total_read_time_seconds > 0" in sql
    assert "total_read_time_seconds DESC" in sql
    assert "days must be greater than 0" in sql
    assert "days must be one of: 7, 30, 90" not in sql
    assert "NOT IN (7, 30, 90)" not in sql
    # ranking order: read time first, then recommendations
    read_time_pos = sql.index("total_read_time_seconds DESC")
    users_pos = sql.index("total_users_recommended DESC", read_time_pos)
    assert users_pos > read_time_pos
    assert "generated_at AT TIME ZONE v_timezone" in sql
    assert "BETWEEN v_start_date AND v_end_date" in sql
    assert "leading_thinker" in sql
    assert "country_perspective" in sql
    assert "influential_research" in sql
    assert "latest_research" in sql
    assert "beyond_your_field" in sql
    # existing behavior preserved
    assert "registration_trend" in sql
    assert "dau_trend" in sql
    assert "distribution" in sql
