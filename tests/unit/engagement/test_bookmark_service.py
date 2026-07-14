from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from apps.engagement.schemas import BookmarkRequest
from apps.engagement.services import bookmark_service as svc


def _post():
    return SimpleNamespace(id=uuid.uuid4())


def _bookmark():
    return SimpleNamespace(id=uuid.uuid4())


@pytest.mark.asyncio
async def test_update_bookmark_creates_bookmark(mock_db, scalar_result):
    post = _post()
    user_id = uuid.uuid4()
    payload = BookmarkRequest(post_id=post.id, is_bookmarked=True)
    db = mock_db(scalar_result(post), scalar_result(None))

    with patch.object(svc, "create_bookmark", AsyncMock(return_value=_bookmark())) as create_bookmark:
        response = await svc.update_bookmark(db, user_id, payload)

    assert response.status is True
    assert response.message == "Bookmark updated successfully"
    assert response.data.is_bookmarked is True
    create_bookmark.assert_awaited_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_bookmark_create_is_idempotent(mock_db, scalar_result):
    post = _post()
    user_id = uuid.uuid4()
    payload = BookmarkRequest(post_id=post.id, is_bookmarked=True)
    db = mock_db(scalar_result(post), scalar_result(_bookmark()))

    with patch.object(svc, "create_bookmark", AsyncMock()) as create_bookmark:
        response = await svc.update_bookmark(db, user_id, payload)

    assert response.status is True
    assert response.data.is_bookmarked is True
    create_bookmark.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_update_bookmark_removes_bookmark(mock_db, scalar_result):
    post = _post()
    user_id = uuid.uuid4()
    bookmark = _bookmark()
    payload = BookmarkRequest(post_id=post.id, is_bookmarked=False)
    db = mock_db(scalar_result(post), scalar_result(bookmark))

    with patch.object(svc, "delete_bookmark", AsyncMock()) as delete_bookmark:
        response = await svc.update_bookmark(db, user_id, payload)

    assert response.status is True
    assert response.data.is_bookmarked is False
    delete_bookmark.assert_awaited_once_with(db, bookmark)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_bookmark_remove_is_idempotent(mock_db, scalar_result):
    post = _post()
    user_id = uuid.uuid4()
    payload = BookmarkRequest(post_id=post.id, is_bookmarked=False)
    db = mock_db(scalar_result(post), scalar_result(None))

    with patch.object(svc, "delete_bookmark", AsyncMock()) as delete_bookmark:
        response = await svc.update_bookmark(db, user_id, payload)

    assert response.status is True
    assert response.data.is_bookmarked is False
    delete_bookmark.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_update_bookmark_post_not_found(mock_db, scalar_result):
    payload = BookmarkRequest(post_id=uuid.uuid4(), is_bookmarked=True)
    db = mock_db(scalar_result(None))

    response = await svc.update_bookmark(db, uuid.uuid4(), payload)

    assert response.status is False
    assert response.message == "Post does not exist"


@pytest.mark.asyncio
async def test_format_post_detail_includes_is_bookmarked():
    from types import SimpleNamespace

    from apps.feed.services.post_service import format_post_detail

    post = SimpleNamespace(
        id=uuid.uuid4(),
        author_user_id=uuid.uuid4(),
        state=SimpleNamespace(value="published"),
        revision_number=1,
        content={"caption": "hello", "content_html": "<p>hello</p>", "visibility": "public"},
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        like_count=1,
        repost_count=0,
        is_moderator_reviewed=False,
        reviewed_at=None,
        moderator_id=None,
        attachments=[],
    )

    data = format_post_detail(post, is_bookmarked=True)
    assert data["is_bookmarked"] is True


@pytest.mark.asyncio
async def test_fetch_post_engagement_flags_includes_bookmarks(mock_db, scalar_result):
    from apps.engagement.repositories.engagement_repository import fetch_post_engagement_flags
    from tests.unit.conftest import FakeScalarResult

    user_id = uuid.uuid4()
    post_a = uuid.uuid4()
    post_b = uuid.uuid4()
    profile_id = uuid.uuid4()

    db = mock_db(
        FakeScalarResult(values=[]),
        FakeScalarResult(values=[post_a]),
        scalar_result(profile_id),
        FakeScalarResult(values=[]),
    )

    flags = await fetch_post_engagement_flags(db, user_id, [post_a, post_b])

    assert post_a in flags.bookmarked_post_ids
    assert post_b not in flags.bookmarked_post_ids
    assert db.execute.await_count == 4
