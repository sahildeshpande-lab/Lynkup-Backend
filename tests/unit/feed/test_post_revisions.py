from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from apps.feed import routes as feed_routes
from apps.feed.services import revision_service as rev_svc
from common.enums import PostState
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


def test_postrevision_route(monkeypatch) -> None:
    post_id = uuid.uuid4()
    created_at = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)

    async def _mock_list(_db, requested_post_id):
        assert requested_post_id == post_id
        return [
            {
                "id": uuid.uuid4(),
                "post_id": post_id,
                "editor_user_id": uuid.uuid4(),
                "editor_name": "Ada Lovelace",
                "content": {"caption": "hi", "visibility": "public"},
                "media": [],
                "changes": {"caption": {"from": "old", "to": "hi"}},
                "media_changed": False,
                "created_at": created_at,
            }
        ]

    monkeypatch.setattr(feed_routes, "list_post_revisions_service", _mock_list)

    response = client.get("/api/v1/postrevision", params={"post_id": str(post_id)})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Post revisions fetched successfully"
    assert len(body["data"]) == 1
    item = body["data"][0]
    assert "triggered_moderation_review" not in item
    assert item["editor_name"] == "Ada Lovelace"
    assert item["media_changed"] is False
    assert "changes" in item


@pytest.mark.asyncio
async def test_create_revision_sets_triggered_flag_on_flagged_to_processing():
    db = AsyncMock()
    post_id = uuid.uuid4()
    editor_id = uuid.uuid4()
    post = SimpleNamespace(
        id=post_id,
        content={"caption": "fixed"},
        state=PostState.processing,
    )

    added = []

    def _add(obj):
        added.append(obj)

    db.add = _add

    with patch.object(
        rev_svc,
        "_build_media_snapshot",
        AsyncMock(return_value=[]),
    ):
        await rev_svc._create_revision(
            post,
            editor_id,
            db,
            previous_state=PostState.flagged,
        )

    assert len(added) == 1
    revision = added[0]
    assert revision.triggered_moderation_review is True
    assert revision.post_id == post_id
    assert revision.editor_user_id == editor_id


@pytest.mark.asyncio
async def test_create_revision_skips_flag_for_subsequent_processing_edits():
    db = AsyncMock()
    post = SimpleNamespace(
        id=uuid.uuid4(),
        content={"caption": "again"},
        state=PostState.processing,
    )
    added = []
    db.add = added.append

    with patch.object(
        rev_svc,
        "_build_media_snapshot",
        AsyncMock(return_value=[]),
    ):
        await rev_svc._create_revision(
            post,
            uuid.uuid4(),
            db,
            previous_state=PostState.processing,
        )

    assert added[0].triggered_moderation_review is False


@pytest.mark.asyncio
async def test_list_post_revisions_omits_triggered_field():
    db = AsyncMock()
    post_id = uuid.uuid4()
    older = SimpleNamespace(
        id=uuid.uuid4(),
        post_id=post_id,
        editor_user_id=uuid.uuid4(),
        content={"caption": "old", "visibility": "public"},
        media=[],
        triggered_moderation_review=False,
        created_at=datetime(2026, 8, 6, 10, 0, tzinfo=timezone.utc),
    )
    newer = SimpleNamespace(
        id=uuid.uuid4(),
        post_id=post_id,
        editor_user_id=uuid.uuid4(),
        content={"caption": "new", "visibility": "public"},
        media=[],
        triggered_moderation_review=True,
        created_at=datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc),
    )

    post_result = MagicMock()
    post_result.scalar_one_or_none.return_value = SimpleNamespace(id=post_id)

    rev_result = MagicMock()
    rev_result.all.return_value = [
        (newer, SimpleNamespace(email="a@example.com"), SimpleNamespace(first_name="Ada", last_name="L")),
        (older, None, None),
    ]

    db.execute = AsyncMock(side_effect=[post_result, rev_result])

    items = await rev_svc.list_post_revisions_service(db, post_id)

    assert len(items) == 2
    assert "triggered_moderation_review" not in items[0]
    assert items[0]["changes"]["caption"] == {"from": "old", "to": "new"}
    assert items[0]["media_changed"] is False
