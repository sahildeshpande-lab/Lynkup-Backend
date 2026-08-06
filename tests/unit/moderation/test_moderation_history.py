from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from apps.moderation import routes as moderation_routes
from apps.moderation.services import moderation_history_service as history_svc
from common.enums import ReportEntityType
from common.exceptions import ApiError
from core.database.session import get_session
from core.security.auth import get_current_user_moderator_or_superadmin
from entrypoints.api import app

client = TestClient(app)


class _NoopSession:
    pass


async def _override_session():
    yield _NoopSession()


@pytest.fixture(autouse=True)
def _override_deps():
    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_user_moderator_or_superadmin] = lambda: SimpleNamespace(
        id=uuid.uuid4(),
        role="moderator",
    )
    yield
    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(get_current_user_moderator_or_superadmin, None)


def test_get_history_route(monkeypatch) -> None:
    entity_id = uuid.uuid4()
    taken_at = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)

    async def _mock_list(_db, requested_entity_id):
        assert requested_entity_id == entity_id
        return [
            {
                "id": uuid.uuid4(),
                "entity_id": entity_id,
                "action_taken": "flagged",
                "moderator_name": "Mod One",
                "comment": "policy violation",
                "action_taken_at": taken_at,
            }
        ]

    monkeypatch.setattr(moderation_routes, "list_moderation_history_service", _mock_list)

    response = client.get("/api/v1/history", params={"entity_id": str(entity_id)})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Moderation history fetched successfully"
    assert len(body["data"]) == 1
    item = body["data"][0]
    assert item["entity_id"] == str(entity_id)
    assert item["action_taken"] == "flagged"
    assert item["moderator_name"] == "Mod One"
    assert item["comment"] == "policy violation"
    assert "action_taken_at" in item


def test_get_history_route_requires_entity_id() -> None:
    response = client.get("/api/v1/history")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] is False
    assert "entity_id" in body["message"].lower()


@pytest.mark.asyncio
async def test_record_moderation_history_rejects_comment_entity():
    db = AsyncMock()
    with pytest.raises(ApiError, match="post and user"):
        await history_svc.record_moderation_history(
            db,
            entity_type=ReportEntityType.comment,
            entity_id=uuid.uuid4(),
            action="deleted",
        )


@pytest.mark.asyncio
async def test_list_moderation_history_formats_rows():
    db = AsyncMock()
    entity_id = uuid.uuid4()
    history = SimpleNamespace(
        id=uuid.uuid4(),
        entity_id=entity_id,
        action="reinstate",
        comment=None,
        created_at=datetime(2026, 8, 6, tzinfo=timezone.utc),
    )
    moderator = SimpleNamespace(email="mod@example.com")
    profile = SimpleNamespace(first_name="Ada", last_name="Lovelace")

    with patch(
        "apps.moderation.services.moderation_history_service.get_history_by_entity_id",
        AsyncMock(return_value=[(history, moderator, profile)]),
    ):
        rows = await history_svc.list_moderation_history_service(db, entity_id)

    assert rows == [
        {
            "id": history.id,
            "entity_id": entity_id,
            "action_taken": "reinstate",
            "moderator_name": "Ada Lovelace",
            "comment": None,
            "action_taken_at": history.created_at,
        }
    ]
