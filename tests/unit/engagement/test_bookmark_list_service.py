from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from apps.engagement.services import bookmark_list_service as svc


def _post_row():
    post = SimpleNamespace(id=uuid.uuid4(), like_count=1)
    author_profile = SimpleNamespace(
        first_name="Jane",
        last_name="Doe",
        profile_photo_url=None,
    )
    return post, author_profile, None, None


@pytest.mark.asyncio
async def test_list_bookmarked_posts_with_pagination(mock_db):
    user_id = uuid.uuid4()
    db = mock_db()
    rows = [_post_row()]

    with (
        patch.object(svc, "count_user_bookmarks", AsyncMock(return_value=5)),
        patch.object(svc, "fetch_user_bookmarked_posts", AsyncMock(return_value=rows)) as fetch_rows,
        patch.object(svc, "fetch_post_engagement_flags", AsyncMock()) as fetch_flags,
        patch.object(svc, "load_latest_post_reactions", AsyncMock(return_value={})),
        patch.object(svc, "format_post_detail", return_value={"id": rows[0][0].id, "is_bookmarked": True}) as format_post,
        patch.object(svc, "format_user_reaction", return_value="LIKE"),
    ):
        fetch_flags.return_value = SimpleNamespace(
            user_reaction_for=lambda _pid: None,
            reposted_post_ids=set(),
            bookmarked_post_ids={rows[0][0].id},
        )
        response = await svc.list_bookmarked_posts(db, user_id, page=2, page_size=2)

    fetch_rows.assert_awaited_once_with(db, user_id, offset=2, limit=2)
    format_post.assert_called_once()
    assert format_post.call_args.kwargs["is_bookmarked"] is True
    assert response.status is True
    assert response.data.totalItems == 5
    assert response.data.page == 2
    assert response.data.pageSize == 2
    assert response.data.totalPages == 3
    assert len(response.data.items) == 1
    assert response.data.items[0]["is_bookmarked"] is True


@pytest.mark.asyncio
async def test_list_bookmarked_posts_without_pagination_returns_all(mock_db):
    user_id = uuid.uuid4()
    db = mock_db()
    rows = [_post_row()]

    with (
        patch.object(svc, "count_user_bookmarks", AsyncMock(return_value=5)) as count_bookmarks,
        patch.object(svc, "fetch_user_bookmarked_posts", AsyncMock(return_value=rows)) as fetch_rows,
        patch.object(svc, "fetch_post_engagement_flags", AsyncMock()) as fetch_flags,
        patch.object(svc, "load_latest_post_reactions", AsyncMock(return_value={})),
        patch.object(svc, "format_post_detail", return_value={"id": rows[0][0].id, "is_bookmarked": True}),
        patch.object(svc, "format_user_reaction", return_value="LIKE"),
    ):
        fetch_flags.return_value = SimpleNamespace(
            user_reaction_for=lambda _pid: None,
            reposted_post_ids=set(),
            bookmarked_post_ids={rows[0][0].id},
        )
        response = await svc.list_bookmarked_posts(db, user_id)

    count_bookmarks.assert_not_called()
    fetch_rows.assert_awaited_once_with(db, user_id, offset=0, limit=None)
    assert response.data.page == 1
    assert response.data.pageSize == 1
    assert response.data.totalItems == 1
    assert response.data.totalPages == 1


@pytest.mark.asyncio
async def test_list_bookmarked_posts_empty(mock_db):
    user_id = uuid.uuid4()
    db = mock_db()

    with (
        patch.object(svc, "count_user_bookmarks", AsyncMock(return_value=0)),
        patch.object(svc, "fetch_user_bookmarked_posts", AsyncMock(return_value=[])),
        patch.object(svc, "fetch_post_engagement_flags", AsyncMock()),
        patch.object(svc, "load_latest_post_reactions", AsyncMock(return_value={})),
    ):
        response = await svc.list_bookmarked_posts(db, user_id, page=1, page_size=20)

    assert response.status is True
    assert response.message == "No bookmarked posts found"
    assert response.data.items == []
    assert response.data.totalItems == 0
