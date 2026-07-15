from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from apps.engagement.services import liked_posts_list_service as svc
from common.enums import ReactionType


def _post_row():
    post = SimpleNamespace(id=uuid.uuid4(), like_count=1)
    author_profile = SimpleNamespace(
        first_name="Jane",
        last_name="Doe",
        profile_photo_url=None,
    )
    return post, author_profile, None, None


@pytest.mark.asyncio
async def test_list_liked_posts_with_pagination(mock_db):
    user_id = uuid.uuid4()
    db = mock_db()
    rows = [_post_row()]

    with (
        patch.object(svc, "count_user_liked_posts", AsyncMock(return_value=3)),
        patch.object(svc, "fetch_user_liked_posts", AsyncMock(return_value=rows)) as fetch_rows,
        patch.object(svc, "fetch_post_engagement_flags", AsyncMock()) as fetch_flags,
        patch.object(svc, "load_latest_post_reactions", AsyncMock(return_value={})),
        patch.object(svc, "format_post_detail", return_value={"id": rows[0][0].id, "is_liked": True}) as format_post,
        patch.object(svc, "format_user_reaction", return_value="LIKE"),
    ):
        fetch_flags.return_value = SimpleNamespace(
            user_reaction_for=lambda _pid: ReactionType.like,
            reposted_post_ids=set(),
            bookmarked_post_ids=set(),
        )
        response = await svc.list_liked_posts(db, user_id, page=1, page_size=10)

    fetch_rows.assert_awaited_once_with(db, user_id, offset=0, limit=10)
    format_post.assert_called_once()
    assert format_post.call_args.kwargs["is_liked"] is True
    assert response.status is True
    assert response.data.totalItems == 3
    assert response.data.items[0]["is_liked"] is True


@pytest.mark.asyncio
async def test_list_liked_posts_without_pagination_returns_all(mock_db):
    user_id = uuid.uuid4()
    db = mock_db()

    with (
        patch.object(svc, "count_user_liked_posts", AsyncMock(return_value=3)) as count_liked,
        patch.object(svc, "fetch_user_liked_posts", AsyncMock(return_value=[])) as fetch_rows,
        patch.object(svc, "fetch_post_engagement_flags", AsyncMock()),
        patch.object(svc, "load_latest_post_reactions", AsyncMock(return_value={})),
    ):
        response = await svc.list_liked_posts(db, user_id)

    count_liked.assert_not_called()
    fetch_rows.assert_awaited_once_with(db, user_id, offset=0, limit=None)
    assert response.message == "No liked posts found"
    assert response.data.items == []
