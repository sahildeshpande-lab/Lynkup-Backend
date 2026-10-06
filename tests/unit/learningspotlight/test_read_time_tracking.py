"""Focused tests for Learning Spotlight read-time tracking."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from apps.accounts.db_models import User
from apps.learningspotlight.db_models.learning_paper_interaction_db_model import (
    LearningPaperInteraction,
)
from apps.learningspotlight.routes import router as spotlight_router
from apps.learningspotlight.services.admin_logs_service import (
    _grouped_logs_order_by,
    list_learning_spotlight_logs,
)
from apps.learningspotlight.services.spotlight_persistence_service import (
    SpotlightPersistenceService,
)
from apps.profiles.db_models.profile_db_model import Profile
from common.enums import LearningPaperAction
from core.database.session import get_session
from core.security.auth import get_current_admin, get_current_user
from tests.unit.conftest import FakeScalarResult


START = datetime.now(timezone.utc)


def _make_dummy_spotlight_dict(paper_id: str = "paper_read_time_1") -> dict:
    return {
        "version": 2,
        "cycle_day": 1,
        "spotlight_type": "leading_thinker",
        "query": '("test")',
        "id": "11111111-1111-1111-1111-111111111111",
        "generated_at": START.isoformat(),
        "paper": {
            "paper_id": paper_id,
            "title": "Read Time Paper",
            "year": 2026,
        },
        "engagement": {
            "is_read": False,
            "is_saved": False,
            "feedback": None,
        },
    }


def _added_interactions(mock_session) -> list[LearningPaperInteraction]:
    return [
        call[0][0]
        for call in mock_session.add.call_args_list
        if isinstance(call[0][0], LearningPaperInteraction)
    ]


def _build_user_app(mock_user: User, db):
    app = FastAPI()
    app.include_router(spotlight_router, prefix="/api/v1")

    async def _override_user():
        return mock_user

    async def _override_db():
        yield db

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_session] = _override_db
    return app


@pytest.mark.asyncio
async def test_read_with_timestamps_stores_duration(mock_db) -> None:
    user_id = uuid4()
    profile = Profile(user_id=user_id, learning_spotlight=_make_dummy_spotlight_dict())
    db = mock_db(FakeScalarResult(profile))

    app = _build_user_app(User(id=user_id, email="reader@example.com"), db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.patch(
            "/api/v1/learning-spotlight/read",
            json={
                "paper_id": "paper_read_time_1",
                "is_read": True,
                "start_time": "2026-09-14T10:00:00.000Z",
                "end_time": "2026-09-14T10:02:22.000Z",
            },
        )

    assert resp.status_code == 200
    interactions = _added_interactions(db)
    assert len(interactions) == 1
    assert interactions[0].action == LearningPaperAction.read.value
    assert interactions[0].read_time_seconds == 142


@pytest.mark.asyncio
async def test_read_missing_one_timestamp_returns_400(mock_db) -> None:
    user_id = uuid4()
    db = mock_db()
    app = _build_user_app(User(id=user_id, email="reader@example.com"), db)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.patch(
            "/api/v1/learning-spotlight/read",
            json={"is_read": True, "start_time": "2026-09-14T10:00:00.000Z"},
        )

    assert resp.status_code == 400
    assert "start_time and end_time" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_read_end_time_before_start_time_returns_400(mock_db) -> None:
    user_id = uuid4()
    db = mock_db()
    app = _build_user_app(User(id=user_id, email="reader@example.com"), db)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.patch(
            "/api/v1/learning-spotlight/read",
            json={
                "is_read": True,
                "start_time": "2026-09-14T10:02:00.000Z",
                "end_time": "2026-09-14T10:00:00.000Z",
            },
        )

    assert resp.status_code == 400
    assert "end_time" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_second_read_creates_another_interaction(mock_db) -> None:
    user_id = uuid4()
    spotlight_dict = _make_dummy_spotlight_dict()
    profile = Profile(user_id=user_id, learning_spotlight=spotlight_dict)

    mock_session = mock_db()
    mock_execute_result = MagicMock()
    mock_execute_result.scalar_one_or_none.return_value = profile
    mock_session.execute = AsyncMock(return_value=mock_execute_result)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()

    service = SpotlightPersistenceService()

    await service.update_read_status(
        mock_session,
        user_id,
        is_read=True,
        read_time_seconds=120,
    )
    await service.update_read_status(
        mock_session,
        user_id,
        is_read=True,
        read_time_seconds=300,
    )

    interactions = _added_interactions(mock_session)
    assert len(interactions) == 2
    assert [interaction.read_time_seconds for interaction in interactions] == [120, 300]


@pytest.mark.asyncio
async def test_read_unknown_paper_id_returns_404(mock_db) -> None:
    user_id = uuid4()
    profile = Profile(user_id=user_id, learning_spotlight=_make_dummy_spotlight_dict())
    db = mock_db(FakeScalarResult(profile), FakeScalarResult(values=[]))
    app = _build_user_app(User(id=user_id, email="reader@example.com"), db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.patch(
            "/api/v1/learning-spotlight/read",
            json={
                "paper_id": "missing-paper",
                "is_read": True,
                "start_time": "2026-09-14T10:00:00.000Z",
                "end_time": "2026-09-14T10:02:22.000Z",
            },
        )

    assert resp.status_code == 404
    assert _added_interactions(db) == []


def _grouped_row(**kwargs):
    defaults = {
        "user_id": uuid4(),
        "paper_id": "paper-123",
        "created_at": START,
        "is_saved": False,
        "is_summarizes": False,
        "is_sythesis": False,
        "is_read": True,
        "is_like": None,
        "is_skip": True,
        "read_time_seconds": 0,
        "email": "ada@example.com",
        "first_name": "Ada",
        "last_name": "Lovelace",
        "university_id": None,
        "major": None,
        "major_id": None,
        "minor": None,
        "minor_id": None,
        "country_id": None,
        "edu_level": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _summary_row() -> SimpleNamespace:
    return SimpleNamespace(
        is_saved=0,
        is_summarizes=0,
        is_sythesis=0,
        is_read=1,
        is_like_true=0,
        is_like_false=0,
        is_like_null=1,
        is_skip=1,
    )


@pytest.mark.asyncio
async def test_admin_returns_cumulative_read_time_seconds(mock_db) -> None:
    user_id = uuid4()
    row = _grouped_row(user_id=user_id, read_time_seconds=420)
    session = mock_db(
        FakeScalarResult(value=1),
        FakeScalarResult(values=[_summary_row()]),
        FakeScalarResult(values=[row]),
        FakeScalarResult(values=[]),
    )

    data = await list_learning_spotlight_logs(session)

    assert data["items"][0]["read_time_seconds"] == 420


def test_admin_sort_read_time_seconds_desc() -> None:
    filtered = SimpleNamespace(
        c=SimpleNamespace(
            read_time_seconds=SimpleNamespace(desc=lambda: "read_time_seconds_desc"),
            created_at=SimpleNamespace(desc=lambda: "created_at_desc"),
            user_id=SimpleNamespace(desc=lambda: "user_id_desc"),
            paper_id=SimpleNamespace(desc=lambda: "paper_id_desc"),
        )
    )
    order_by = _grouped_logs_order_by(
        filtered,
        sort_by="read_time_seconds",
        order="desc",
    )
    assert order_by[0] == "read_time_seconds_desc"


def test_admin_sort_read_time_seconds_asc() -> None:
    filtered = SimpleNamespace(
        c=SimpleNamespace(
            read_time_seconds=SimpleNamespace(asc=lambda: "read_time_seconds_asc"),
            created_at=SimpleNamespace(desc=lambda: "created_at_desc"),
            user_id=SimpleNamespace(desc=lambda: "user_id_desc"),
            paper_id=SimpleNamespace(desc=lambda: "paper_id_desc"),
        )
    )
    order_by = _grouped_logs_order_by(
        filtered,
        sort_by="read_time_seconds",
        order="asc",
    )
    assert order_by[0] == "read_time_seconds_asc"


@pytest.mark.asyncio
async def test_admin_list_logs_forwards_sort_params(mock_db) -> None:
    session = mock_db(
        FakeScalarResult(value=0),
        FakeScalarResult(values=[_summary_row()]),
        FakeScalarResult(values=[]),
    )

    with patch(
        "apps.learningspotlight.services.admin_logs_service._grouped_logs_order_by",
        wraps=_grouped_logs_order_by,
    ) as order_by:
        await list_learning_spotlight_logs(
            session,
            sort_by="read_time_seconds",
            order="desc",
        )

    assert order_by.call_args.kwargs == {
        "sort_by": "read_time_seconds",
        "order": "desc",
    }
