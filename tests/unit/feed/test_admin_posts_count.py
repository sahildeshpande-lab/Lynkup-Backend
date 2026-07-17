from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.feed.services import post_service as svc
from common.enums import PostState


def _post(*, state: PostState = PostState.published):
    return SimpleNamespace(
        id=uuid.uuid4(),
        author_user_id=uuid.uuid4(),
        state=state,
        content={"visibility": "public"},
        moderator_id=None,
        is_moderator_reviewed=False,
        reviewed_at=None,
        revision_number=1,
        updated_at=None,
    )


@pytest.mark.asyncio
async def test_admin_flag_decrements_posts_count(mock_db):
    post = _post(state=PostState.published)
    admin_id = uuid.uuid4()
    db = mock_db()

    post_result = MagicMock()
    post_result.scalar_one_or_none.return_value = post
    author_result = MagicMock()
    author_result.first.return_value = None
    db.execute = AsyncMock(side_effect=[post_result, author_result])

    with (
        patch.object(svc, "_create_revision", AsyncMock()),
        patch(
            "apps.profiles.services.profile_stats_service.decrement_posts_count_for_user",
            AsyncMock(),
        ) as dec,
        patch(
            "apps.profiles.services.profile_stats_service.increment_posts_count_for_user",
            AsyncMock(),
        ) as inc,
    ):
        result = await svc.admin_publish_post_service(post.id, "flagged", admin_id, db)

    assert result.state == PostState.flagged
    dec.assert_awaited_once_with(db, post.author_user_id)
    inc.assert_not_called()


@pytest.mark.asyncio
async def test_admin_publish_from_flagged_increments_posts_count(mock_db):
    post = _post(state=PostState.flagged)
    admin_id = uuid.uuid4()
    db = mock_db()

    post_result = MagicMock()
    post_result.scalar_one_or_none.return_value = post
    author_result = MagicMock()
    author_result.first.return_value = None
    db.execute = AsyncMock(side_effect=[post_result, author_result])

    with (
        patch.object(svc, "_create_revision", AsyncMock()),
        patch(
            "apps.profiles.services.profile_stats_service.decrement_posts_count_for_user",
            AsyncMock(),
        ) as dec,
        patch(
            "apps.profiles.services.profile_stats_service.increment_posts_count_for_user",
            AsyncMock(),
        ) as inc,
    ):
        result = await svc.admin_publish_post_service(post.id, "published", admin_id, db)

    assert result.state == PostState.published
    inc.assert_awaited_once_with(db, post.author_user_id)
    dec.assert_not_called()


@pytest.mark.asyncio
async def test_delete_post_service_decrements_posts_count_for_published(mock_db):
    post = _post(state=PostState.published)
    db = mock_db()

    post_result = MagicMock()
    post_result.scalar_one_or_none.return_value = post
    db.execute = AsyncMock(return_value=post_result)

    with patch(
        "apps.profiles.services.profile_stats_service.decrement_posts_count_for_user",
        AsyncMock(),
    ) as dec:
        result = await svc.delete_post_service(post.id, post.author_user_id, db)

    assert result.state == PostState.deleted
    dec.assert_awaited_once_with(db, post.author_user_id)
    assert db.commit.await_count == 2


@pytest.mark.asyncio
async def test_delete_post_service_skips_decrement_for_draft(mock_db):
    post = _post(state=PostState.draft)
    db = mock_db()

    post_result = MagicMock()
    post_result.scalar_one_or_none.return_value = post
    db.execute = AsyncMock(return_value=post_result)

    with patch(
        "apps.profiles.services.profile_stats_service.decrement_posts_count_for_user",
        AsyncMock(),
    ) as dec:
        result = await svc.delete_post_service(post.id, post.author_user_id, db)

    assert result.state == PostState.deleted
    dec.assert_not_called()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_post_service_idempotent_when_already_deleted(mock_db):
    post = _post(state=PostState.deleted)
    db = mock_db()

    post_result = MagicMock()
    post_result.scalar_one_or_none.return_value = post
    db.execute = AsyncMock(return_value=post_result)

    with patch(
        "apps.profiles.services.profile_stats_service.decrement_posts_count_for_user",
        AsyncMock(),
    ) as dec:
        result = await svc.delete_post_service(post.id, post.author_user_id, db)

    assert result.state == PostState.deleted
    dec.assert_not_called()
    db.commit.assert_not_called()
