"""Unit tests for GET /admin/learning-spotlight-logs grouped aggregation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Boolean, Column, MetaData, String, Table, select

from apps.accounts.db_models import User
from apps.learningspotlight.routes import router as spotlight_router
from apps.learningspotlight.services.admin_logs_service import (
    ALL_LOG_ACTIONS,
    GroupedLogFilters,
    IS_LIKE_FILTER_UNSET,
    _apply_grouped_search_filter,
    _apply_grouped_state_filters,
    collect_paper_titles,
    derive_spotlight_admin_log_group,
    list_learning_spotlight_logs,
    parse_is_like_query_param,
)
from apps.profiles.db_models import Profile
from common.enums import LearningPaperAction
from core.database.session import get_session
from core.security.auth import get_current_admin
from tests.unit.conftest import FakeScalarResult
from apps.administration.dependencies import require_signed_admin

START = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)


def _actions(*names: str, start: datetime = START) -> list[tuple[str, datetime]]:
    return [
        (name, start + timedelta(minutes=index))
        for index, name in enumerate(names)
    ]


def _empty_summary() -> dict:
    return {
        "is_saved": 0,
        "is_summarizes": 0,
        "is_sythesis": 0,
        "is_read": 0,
        "is_like": {"true": 0, "false": 0, "null": 0},
        "is_skip": 0,
    }


def test_all_log_actions_include_read() -> None:
    assert LearningPaperAction.read.value in ALL_LOG_ACTIONS
    assert ALL_LOG_ACTIONS == {
        "READ",
        "SAVE",
        "UNSAVE",
        "LIKE",
        "DISLIKE",
        "SUMMARY",
        "SYNTHESIS",
    }


def test_collect_paper_titles_from_spotlight_payload() -> None:
    titles = collect_paper_titles(
        {
            "papers": [{"paper_id": "p1", "title": "First Paper"}, "skip-me"],
            "paper": {"paper_id": "p2", "title": " Legacy Paper "},
        }
    )
    assert titles == {"p1": "First Paper", "p2": "Legacy Paper"}


def test_parse_is_like_query_param() -> None:
    assert parse_is_like_query_param(None) is IS_LIKE_FILTER_UNSET
    assert parse_is_like_query_param("true") is True
    assert parse_is_like_query_param("false") is False
    assert parse_is_like_query_param("null") is None
    with pytest.raises(ValueError):
        parse_is_like_query_param("maybe")


def _grouped_logs_table():
    return Table(
        "grouped_logs",
        MetaData(),
        Column("is_saved", Boolean),
        Column("is_summarizes", Boolean),
        Column("is_sythesis", Boolean),
        Column("is_read", Boolean),
        Column("is_like", Boolean),
        Column("is_skip", Boolean),
    )


def _compiled_where_clause(stmt) -> str:
    where = stmt.whereclause
    assert where is not None
    return str(where.compile(compile_kwargs={"literal_binds": True})).lower()


def test_apply_grouped_state_filters_combines_multiple_with_or() -> None:
    grouped = _grouped_logs_table()
    stmt = _apply_grouped_state_filters(
        select(grouped),
        grouped,
        GroupedLogFilters(
            is_saved=True,
            is_summarizes=True,
            is_sythesis=True,
            is_read=True,
            is_like=True,
            is_skip=True,
        ),
    )
    compiled = _compiled_where_clause(stmt)
    assert " or " in compiled
    assert " and " not in compiled
    for column in (
        "is_saved",
        "is_summarizes",
        "is_sythesis",
        "is_read",
        "is_like",
        "is_skip",
    ):
        assert column in compiled


@pytest.mark.asyncio
async def test_apply_grouped_search_filter_includes_university_country_major() -> None:
    grouped = Table(
        "grouped_logs",
        MetaData(),
        Column("user_id", String),
        Column("paper_id", String),
    )
    base = (
        select(grouped, Profile.first_name)
        .select_from(grouped)
        .outerjoin(Profile, Profile.user_id == grouped.c.user_id)
    )
    with patch(
        "apps.learningspotlight.services.admin_logs_service._find_paper_ids_by_title_search",
        new=AsyncMock(return_value=set()),
    ):
        stmt = await _apply_grouped_search_filter(
            AsyncMock(),
            base,
            grouped,
            search="India",
            user_id=None,
        )
    compiled = _compiled_where_clause(stmt)
    assert "universities.name" in compiled
    assert "countries.name" in compiled
    assert "majors.name" in compiled
    assert "profiles.major" in compiled


def test_apply_grouped_state_filters_single_filter_unchanged() -> None:
    grouped = _grouped_logs_table()
    stmt = _apply_grouped_state_filters(
        select(grouped),
        grouped,
        GroupedLogFilters(is_read=True),
    )
    compiled = _compiled_where_clause(stmt)
    assert "is_read" in compiled
    assert " or " not in compiled


def test_apply_grouped_state_filters_skips_unset_filters() -> None:
    grouped = _grouped_logs_table()
    stmt = _apply_grouped_state_filters(
        select(grouped),
        grouped,
        GroupedLogFilters(),
    )
    assert stmt.whereclause is None


@pytest.mark.parametrize(
    ("actions", "expected"),
    [
        (("SAVE", "UNSAVE"), {"is_saved": False}),
        (("SAVE", "UNSAVE", "SAVE"), {"is_saved": True}),
        (("LIKE",), {"is_like": True}),
        (("DISLIKE",), {"is_like": False}),
        (("READ",), {"is_like": None, "is_skip": True, "is_read": True}),
        (("READ", "LIKE"), {"is_like": True, "is_skip": False, "is_read": True}),
        (("READ", "DISLIKE"), {"is_like": False, "is_skip": False, "is_read": True}),
        (("SUMMARY",), {"is_summarizes": True}),
        (("SYNTHESIS",), {"is_sythesis": True}),
        (
            ("READ", "SAVE", "UNSAVE", "LIKE", "DISLIKE", "SAVE"),
            {"is_saved": True, "is_read": True, "is_like": False, "is_skip": False},
        ),
        (("LIKE", "DISLIKE", "LIKE"), {"is_like": True}),
        (("SAVE", "UNSAVE"), {"is_read": False, "is_skip": False}),
    ],
)
def test_derive_spotlight_admin_log_group(actions, expected) -> None:
    derived = derive_spotlight_admin_log_group(_actions(*actions))
    for key, value in expected.items():
        assert derived[key] == value


def test_derive_spotlight_admin_log_group_uses_latest_created_at() -> None:
    derived = derive_spotlight_admin_log_group(
        [
            ("READ", START),
            ("LIKE", START + timedelta(hours=2)),
        ]
    )
    assert derived["created_at"] == START + timedelta(hours=2)


def _grouped_row(
    *,
    user_id=None,
    paper_id="paper-123",
    created_at=None,
    is_saved=False,
    is_summarizes=False,
    is_sythesis=False,
    is_read=False,
    is_like=None,
    is_skip=False,
    email="ada@example.com",
    first_name="Ada",
    last_name="Lovelace",
    university_id=None,
    major=None,
    major_id=None,
    minor=None,
    minor_id=None,
    country_id=None,
    edu_level=None,
):
    return SimpleNamespace(
        user_id=user_id or uuid4(),
        paper_id=paper_id,
        created_at=created_at or START,
        is_saved=is_saved,
        is_summarizes=is_summarizes,
        is_sythesis=is_sythesis,
        is_read=is_read,
        is_like=is_like,
        is_skip=is_skip,
        email=email,
        first_name=first_name,
        last_name=last_name,
        university_id=university_id,
        major=major,
        major_id=major_id,
        minor=minor,
        minor_id=minor_id,
        country_id=country_id,
        edu_level=edu_level,
    )


def _summary_row(**counts) -> SimpleNamespace:
    defaults = _empty_summary()
    like_counts = counts.pop("is_like", None)
    if isinstance(like_counts, dict):
        defaults["is_like"].update(like_counts)
    defaults.update(counts)
    like = defaults["is_like"]
    return SimpleNamespace(
        is_saved=defaults["is_saved"],
        is_summarizes=defaults["is_summarizes"],
        is_sythesis=defaults["is_sythesis"],
        is_read=defaults["is_read"],
        is_like_true=like["true"],
        is_like_false=like["false"],
        is_like_null=like["null"],
        is_skip=defaults["is_skip"],
    )


@pytest.mark.asyncio
async def test_list_learning_spotlight_logs_returns_grouped_item(mock_db) -> None:
    user_id = uuid4()
    row = _grouped_row(
        user_id=user_id,
        is_saved=True,
        is_read=True,
        is_like=True,
    )
    spotlight = {
        "spotlight_type": "leading_thinker",
        "paper": {"paper_id": "paper-123", "title": "Notes on the Analytical Engine"},
    }
    session = mock_db(
        FakeScalarResult(value=1),
        FakeScalarResult(values=[_summary_row(is_saved=1, is_read=1, is_like={"true": 1, "false": 0, "null": 0})]),
        FakeScalarResult(values=[row]),
        FakeScalarResult(values=[SimpleNamespace(user_id=user_id, learning_spotlight=spotlight)]),
    )

    data = await list_learning_spotlight_logs(session)

    assert data["totalItems"] == 1
    item = data["items"][0]
    assert item["user_id"] == str(user_id)
    assert item["paper_title"] == "Notes on the Analytical Engine"
    assert item["recommended_cycle_name"] == "Leading Thinker"
    assert item["is_saved"] is True
    assert item["is_read"] is True
    assert item["is_like"] is True
    assert item["is_skip"] is False
    assert "action" not in item
    assert data["summary"]["is_saved"] == 1
    assert item["university"] is None
    assert item["university_details"] == {
        "id": None,
        "university_name": None,
        "university_website": None,
    }
    assert item["educationLevel"] is None
    assert item["educationLevel_details"] is None


@pytest.mark.asyncio
async def test_list_learning_spotlight_logs_empty_response(mock_db) -> None:
    session = mock_db(
        FakeScalarResult(value=0),
        FakeScalarResult(values=[_summary_row()]),
        FakeScalarResult(values=[]),
    )
    data = await list_learning_spotlight_logs(session)
    assert data["items"] == []
    assert data["totalItems"] == 0
    assert data["totalPages"] == 0
    assert data["summary"] == _empty_summary()


@pytest.mark.asyncio
async def test_list_learning_spotlight_logs_uses_paper_id_when_title_missing(mock_db) -> None:
    user_id = uuid4()
    row = _grouped_row(user_id=user_id, paper_id="unknown-paper", is_summarizes=True)
    session = mock_db(
        FakeScalarResult(value=1),
        FakeScalarResult(values=[_summary_row(is_summarizes=1)]),
        FakeScalarResult(values=[row]),
        FakeScalarResult(values=[]),
        FakeScalarResult(values=[]),
    )

    data = await list_learning_spotlight_logs(session)

    assert data["items"][0]["paper_title"] == "unknown-paper"
    assert data["items"][0]["is_summarizes"] is True
    assert data["items"][0]["recommended_cycle_name"] is None


@pytest.mark.asyncio
async def test_list_learning_spotlight_logs_search_and_pagination(mock_db) -> None:
    user_id = uuid4()
    row = _grouped_row(user_id=user_id, is_read=True, is_like=None, is_skip=True)
    rec_log = SimpleNamespace(
        learning_recommendations={"papers": [{"paper_id": "paper-123", "title": "Related Work Synthesis"}]}
    )
    session = mock_db(
        FakeScalarResult(values=[]),
        FakeScalarResult(values=[rec_log]),
        FakeScalarResult(value=1),
        FakeScalarResult(
            values=[_summary_row(is_read=1, is_skip=1, is_like={"true": 0, "false": 0, "null": 1})]
        ),
        FakeScalarResult(values=[row]),
        FakeScalarResult(values=[]),
        FakeScalarResult(values=[rec_log]),
    )

    data = await list_learning_spotlight_logs(
        session,
        user_id=user_id,
        search="Synthesis",
        filters=GroupedLogFilters(is_skip=True),
        page=1,
        page_size=20,
    )

    assert data["page"] == 1
    assert data["pageSize"] == 20
    assert data["items"][0]["is_skip"] is True
    assert data["items"][0]["is_like"] is None


@pytest.mark.asyncio
async def test_list_learning_spotlight_logs_includes_academic_details(mock_db) -> None:
    user_id = uuid4()
    university_id = uuid4()
    country_id = uuid4()
    row = _grouped_row(
        user_id=user_id,
        university_id=university_id,
        major="Accounting",
        major_id=1,
        minor="Accounting and Finance",
        minor_id=15,
        country_id=country_id,
        edu_level="Bachelors",
    )
    session = mock_db(
        FakeScalarResult(value=1),
        FakeScalarResult(values=[_summary_row()]),
        FakeScalarResult(values=[row]),
        FakeScalarResult(values=[]),
        FakeScalarResult(values=[]),
        FakeScalarResult(
            values=[
                SimpleNamespace(
                    id=university_id,
                    name="42 FR",
                    website="http://www.42.fr/",
                )
            ]
        ),
        FakeScalarResult(values=[SimpleNamespace(id=country_id, name="India")]),
        FakeScalarResult(values=[SimpleNamespace(id=1, name="Accounting")]),
        FakeScalarResult(values=[SimpleNamespace(id=15, name="Accounting and Finance")]),
    )

    data = await list_learning_spotlight_logs(session)
    item = data["items"][0]

    assert item["university"] == "42 FR"
    assert item["university_details"] == {
        "id": str(university_id),
        "university_name": "42 FR",
        "university_website": "http://www.42.fr/",
    }
    assert item["major"] == "Accounting"
    assert item["major_details"] == {"id": 1, "major_name": "Accounting"}
    assert item["minor"] == "Accounting and Finance"
    assert item["minor_details"] == {"id": 15, "minor_name": "Accounting and Finance"}
    assert item["country"] == str(country_id)
    assert item["country_details"] == {
        "id": str(country_id),
        "country_name": "India",
    }
    assert item["educationLevel"] == "Bachelors"
    assert item["educationLevel_details"] == {"id": 1, "edu_level": "Bachelors"}


@pytest.mark.asyncio
async def test_list_learning_spotlight_logs_forwards_boolean_filters(mock_db) -> None:
    session = mock_db(
        FakeScalarResult(value=0),
        FakeScalarResult(values=[_summary_row()]),
        FakeScalarResult(values=[]),
    )

    with patch(
        "apps.learningspotlight.services.admin_logs_service._apply_grouped_state_filters",
        wraps=lambda base, grouped_subq, filters: base,
    ) as apply_filters:
        await list_learning_spotlight_logs(
            session,
            filters=GroupedLogFilters(
                is_saved=True,
                is_read=True,
                is_like=False,
                is_skip=False,
            ),
        )

    passed_filters = apply_filters.call_args.args[2]
    assert passed_filters.is_saved is True
    assert passed_filters.is_read is True
    assert passed_filters.is_like is False
    assert passed_filters.is_skip is False


@pytest.mark.asyncio
async def test_list_learning_spotlight_logs_is_like_null_filter(mock_db) -> None:
    session = mock_db(
        FakeScalarResult(value=0),
        FakeScalarResult(values=[_summary_row()]),
        FakeScalarResult(values=[]),
    )

    with patch(
        "apps.learningspotlight.services.admin_logs_service._apply_grouped_state_filters",
        wraps=lambda base, grouped_subq, filters: base,
    ) as apply_filters:
        await list_learning_spotlight_logs(
            session,
            filters=GroupedLogFilters(is_like=None),
        )

    assert apply_filters.call_args.args[2].is_like is None


@pytest.mark.asyncio
async def test_admin_learning_spotlight_logs_endpoint(mock_db) -> None:
    admin_id = uuid4()
    user_id = uuid4()
    mock_admin = User(id=admin_id, email="admin@example.com", role="superadmin")

    app = FastAPI()
    app.include_router(spotlight_router, prefix="/api/v1")

    async def _override_admin():
        return mock_admin

    async def _override_db():
        yield mock_db()

    app.dependency_overrides[get_current_admin] = _override_admin
    app.dependency_overrides[require_signed_admin] = _override_admin
    app.dependency_overrides[get_session] = _override_db

    payload = {
        "items": [
            {
                "user_id": str(user_id),
                "first_name": "Ada",
                "last_name": "Lovelace",
                "email": "ada@example.com",
                "paper_title": "Computing Machinery and Intelligence",
                "paper_id": "paper-abc",
                "is_saved": True,
                "is_summarizes": False,
                "is_sythesis": False,
                "is_read": True,
                "is_like": True,
                "is_skip": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ],
        "page": 1,
        "pageSize": 1,
        "totalItems": 1,
        "totalPages": 1,
        "summary": {
            "is_saved": 1,
            "is_summarizes": 0,
            "is_sythesis": 0,
            "is_read": 1,
            "is_like": {"true": 1, "false": 0, "null": 0},
            "is_skip": 0,
        },
    }

    with patch(
        "apps.learningspotlight.routes.list_learning_spotlight_logs",
        new=AsyncMock(return_value=payload),
    ) as mocked:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/admin/learning-spotlight-logs",
                params={
                    "user_id": str(user_id),
                    "is_read": "true",
                    "is_like": "true",
                },
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] is True
    assert body["data"]["items"][0]["is_like"] is True
    assert body["data"]["summary"]["is_like"]["true"] == 1
    kwargs = mocked.await_args.kwargs
    assert kwargs["user_id"] == user_id
    assert kwargs["filters"].is_read is True
    assert kwargs["filters"].is_like is True


@pytest.mark.asyncio
async def test_admin_learning_spotlight_logs_endpoint_rejects_invalid_is_like(mock_db) -> None:
    admin_id = uuid4()
    mock_admin = User(id=admin_id, email="admin@example.com", role="superadmin")

    app = FastAPI()
    app.include_router(spotlight_router, prefix="/api/v1")
    app.dependency_overrides[get_current_admin] = lambda: mock_admin
    app.dependency_overrides[require_signed_admin] = lambda: mock_admin
    app.dependency_overrides[get_session] = lambda: mock_db()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/admin/learning-spotlight-logs",
            params={"is_like": "maybe"},
        )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_admin_learning_spotlight_logs_endpoint_forwards_pagination(mock_db) -> None:
    admin_id = uuid4()
    mock_admin = User(id=admin_id, email="admin@example.com", role="superadmin")

    app = FastAPI()
    app.include_router(spotlight_router, prefix="/api/v1")
    app.dependency_overrides[get_current_admin] = lambda: mock_admin
    app.dependency_overrides[require_signed_admin] = lambda: mock_admin
    app.dependency_overrides[get_session] = lambda: mock_db()

    payload = {
        "items": [],
        "page": 2,
        "pageSize": 10,
        "totalItems": 0,
        "totalPages": 0,
        "summary": _empty_summary(),
    }

    with patch(
        "apps.learningspotlight.routes.list_learning_spotlight_logs",
        new=AsyncMock(return_value=payload),
    ) as mocked:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/admin/learning-spotlight-logs",
                params={"page": 2, "pageSize": 10, "is_like": "null"},
            )

    assert resp.status_code == 200
    kwargs = mocked.await_args.kwargs
    assert kwargs["page"] == 2
    assert kwargs["page_size"] == 10
    assert kwargs["filters"].is_like is None
