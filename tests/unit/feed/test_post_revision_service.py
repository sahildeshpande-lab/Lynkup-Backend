from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from apps.feed.services.revision_service import list_post_revisions_service
from common.enums import PostState
from common.exceptions import ApiError


@pytest.mark.asyncio
async def test_list_post_revisions_includes_status_and_moderation_notes(mock_db, scalar_result):
    post_id = uuid4()
    editor_id = uuid4()
    post = SimpleNamespace(
        id=post_id,
        state=PostState.published,
        moderation_notes="Approved after policy review",
    )
    revision = SimpleNamespace(
        id=uuid4(),
        post_id=post_id,
        editor_user_id=editor_id,
        content={"caption": "Updated caption", "visibility": "public", "content_html": ""},
        media=[
            {
                "id": str(uuid4()),
                "key": "posts/test.jpg",
                "type": "image",
                "original_filename": "test.jpg",
                "mime_type": "image/jpeg",
                "file_size": 1000,
            }
        ],
        created_at=datetime(2026, 8, 26, 5, 30, tzinfo=timezone.utc),
    )
    user = SimpleNamespace(id=editor_id, role="moderator", username="mod_user")
    profile = SimpleNamespace(first_name="Jane", last_name="Doe")

    # mock_db: first query returns post, second query returns revision row
    db = mock_db(
        scalar_result(post),
        scalar_result(values=[(revision, user, profile)]),
    )

    revisions = await list_post_revisions_service(db, post_id)

    assert len(revisions) == 1
    item = revisions[0]
    assert item["id"] == revision.id
    assert item["post_id"] == post_id
    assert item["editor_user_id"] == editor_id
    assert item["editor_name"] == "Jane Doe"
    assert item["status"] == "published"
    assert item["moderation_notes"] == "Approved after policy review"
    assert item["content"]["caption"] == "Updated caption"
    assert item["media"] == revision.media


@pytest.mark.asyncio
async def test_list_post_revisions_null_notes_and_different_states(mock_db, scalar_result):
    post_id = uuid4()
    editor_id = uuid4()
    post = SimpleNamespace(
        id=post_id,
        state=PostState.flagged,
        moderation_notes=None,
    )
    revision = SimpleNamespace(
        id=uuid4(),
        post_id=post_id,
        editor_user_id=editor_id,
        content={"caption": "Draft post", "visibility": "public", "content_html": ""},
        media=None,
        created_at=datetime(2026, 8, 26, 5, 30, tzinfo=timezone.utc),
    )
    user = SimpleNamespace(id=editor_id, role="user", username="user1")

    db = mock_db(
        scalar_result(post),
        scalar_result(values=[(revision, user, None)]),
    )

    revisions = await list_post_revisions_service(db, post_id)

    assert len(revisions) == 1
    item = revisions[0]
    assert item["status"] == "flagged"
    assert item["moderation_notes"] is None


@pytest.mark.asyncio
async def test_list_post_revisions_post_not_found(mock_db, scalar_result):
    post_id = uuid4()
    db = mock_db(scalar_result(None))

    with pytest.raises(ApiError, match="Post not found"):
        await list_post_revisions_service(db, post_id)
