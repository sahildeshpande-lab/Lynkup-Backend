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
        moderation_notes=None,
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

    with (
        patch.object(svc, "_hard_delete_post", AsyncMock(return_value=post.id)) as hard_del,
        patch(
            "apps.profiles.services.profile_stats_service.decrement_posts_count_for_user",
            AsyncMock(),
        ) as dec,
    ):
        result = await svc.delete_post_service(post.id, post.author_user_id, db)

    assert result == {"id": post.id, "deleted": True}
    hard_del.assert_awaited_once()
    dec.assert_awaited_once_with(db, post.author_user_id)
    assert db.commit.await_count == 2


@pytest.mark.asyncio
async def test_delete_post_service_skips_decrement_for_draft(mock_db):
    post = _post(state=PostState.draft)
    db = mock_db()

    post_result = MagicMock()
    post_result.scalar_one_or_none.return_value = post
    db.execute = AsyncMock(return_value=post_result)

    with (
        patch.object(svc, "_hard_delete_post", AsyncMock(return_value=post.id)),
        patch(
            "apps.profiles.services.profile_stats_service.decrement_posts_count_for_user",
            AsyncMock(),
        ) as dec,
    ):
        result = await svc.delete_post_service(post.id, post.author_user_id, db)

    assert result == {"id": post.id, "deleted": True}
    dec.assert_not_called()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_post_service_not_found_when_missing(mock_db):
    db = mock_db()
    post_result = MagicMock()
    post_result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=post_result)

    with pytest.raises(Exception) as exc_info:
        await svc.delete_post_service(uuid.uuid4(), uuid.uuid4(), db)

    assert "not found" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_admin_reject_hard_deletes_post(mock_db):
    post = _post(state=PostState.published)
    admin_id = uuid.uuid4()
    db = mock_db()

    post_result = MagicMock()
    post_result.scalar_one_or_none.return_value = post
    db.execute = AsyncMock(return_value=post_result)

    with (
        patch.object(svc, "_hard_delete_post", AsyncMock(return_value=post.id)),
        patch(
            "apps.profiles.services.profile_stats_service.decrement_posts_count_for_user",
            AsyncMock(),
        ) as dec,
    ):
        result = await svc.admin_publish_post_service(
            post.id, "rejected", admin_id, db
        )

    assert result == {"id": post.id, "deleted": True, "status": "rejected"}
    dec.assert_awaited_once_with(db, post.author_user_id)

@pytest.mark.asyncio
async def test_admin_escalate_assigns_superadmin_and_stores_notes(mock_db):
    post = _post(state=PostState.published)
    admin_id = uuid.uuid4()
    superadmin_id = uuid.uuid4()
    db = mock_db()

    post_result = MagicMock()
    post_result.scalar_one_or_none.return_value = post
    author_result = MagicMock()
    author_result.first.return_value = None
    db.execute = AsyncMock(side_effect=[post_result, author_result])

    with (
        patch.object(svc, "_create_revision", AsyncMock()),
        patch.object(
            svc,
            "_fetch_superadmin_user_ids",
            AsyncMock(return_value=[superadmin_id]),
        ),
        patch(
            "apps.profiles.services.profile_stats_service.decrement_posts_count_for_user",
            AsyncMock(),
        ) as dec,
        patch(
            "apps.profiles.services.profile_stats_service.increment_posts_count_for_user",
            AsyncMock(),
        ) as inc,
    ):
        result = await svc.admin_publish_post_service(
            post.id,
            "escalate",
            admin_id,
            db,
            notes="Needs senior review on policy",
        )

    assert result.state == PostState.escalate
    assert result.moderator_id == superadmin_id
    assert result.moderation_notes == "Needs senior review on policy"
    assert result.is_moderator_reviewed is True
    dec.assert_awaited_once_with(db, post.author_user_id)
    inc.assert_not_called()


@pytest.mark.asyncio
async def test_admin_escalate_requires_notes(mock_db):
    post = _post(state=PostState.published)
    admin_id = uuid.uuid4()
    db = mock_db()

    post_result = MagicMock()
    post_result.scalar_one_or_none.return_value = post
    author_result = MagicMock()
    author_result.first.return_value = None
    db.execute = AsyncMock(side_effect=[post_result, author_result])

    with pytest.raises(Exception) as exc_info:
        await svc.admin_publish_post_service(
            post.id,
            "escalate",
            admin_id,
            db,
            notes="   ",
        )

    assert "notes" in str(exc_info.value).lower()
