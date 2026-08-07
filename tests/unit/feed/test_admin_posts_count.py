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
            "apps.moderation.services.record_moderation_history",
            AsyncMock(),
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
        patch("apps.moderation.services.record_moderation_history", AsyncMock()),
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
        patch("apps.moderation.services.record_moderation_history", AsyncMock()),
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
        patch("apps.moderation.services.record_moderation_history", AsyncMock()),
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
async def test_admin_escalate_allows_blank_notes(mock_db):
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
        patch("apps.moderation.services.record_moderation_history", AsyncMock()),
        patch.object(
            svc,
            "_fetch_superadmin_user_ids",
            AsyncMock(return_value=[superadmin_id]),
        ),
        patch(
            "apps.profiles.services.profile_stats_service.decrement_posts_count_for_user",
            AsyncMock(),
        ),
        patch(
            "apps.profiles.services.profile_stats_service.increment_posts_count_for_user",
            AsyncMock(),
        ),
    ):
        result = await svc.admin_publish_post_service(
            post.id,
            "escalate",
            admin_id,
            db,
            notes="   ",
        )

    assert result.state == PostState.escalate
    assert result.moderation_notes is None


@pytest.mark.asyncio
async def test_edit_flagged_post_moves_to_processing_keeps_moderator(mock_db):
    from apps.feed.schemas import EditPostContentPayload, EditPostRequest

    moderator_id = uuid.uuid4()
    author_id = uuid.uuid4()
    post = SimpleNamespace(
        id=uuid.uuid4(),
        author_user_id=author_id,
        state=PostState.flagged,
        content={"caption": "Needs edit", "visibility": "public"},
        moderator_id=moderator_id,
        is_moderator_reviewed=True,
        reviewed_at=object(),
        revision_number=2,
        updated_at=None,
        is_edited=False,
        attachments=[],
    )
    db = mock_db()
    post_result = MagicMock()
    post_result.scalar_one_or_none.return_value = post
    db.execute = AsyncMock(return_value=post_result)

    payload = EditPostRequest(
        id=post.id,
        content=EditPostContentPayload(caption="Edited after flag"),
    )

    with (
        patch.object(svc, "_sync_hashtags", AsyncMock()),
        patch.object(svc, "_create_revision", AsyncMock()),
        patch.object(svc, "_assign_moderator_for_review", AsyncMock()) as assign_mod,
        patch.object(svc, "log_post_keywords_best_effort", AsyncMock()),
        patch.object(svc, "_should_sync_topics_for_post", return_value=False),
    ):
        result = await svc.edit_post_service(author_id, payload, db)

    assert result.state == PostState.processing
    assert result.moderator_id == moderator_id
    assert result.is_moderator_reviewed is False
    assert result.reviewed_at is None
    assert result.is_edited is True
    assert result.content["caption"] == "Edited after flag"
    assign_mod.assert_awaited_once()
    assert assign_mod.await_args.args[0] is post
    assert assign_mod.await_args.kwargs["previous_state"] == PostState.flagged


@pytest.mark.asyncio
async def test_query_states_owner_published_includes_processing():
    from common.enums import FEED_VISIBLE_POST_STATES, OWNER_VISIBLE_POST_STATES

    owner_states = svc._query_states_for_list(PostState.published, is_owner=True)
    visitor_states = svc._query_states_for_list(PostState.published, is_owner=False)

    assert owner_states == OWNER_VISIBLE_POST_STATES
    assert PostState.processing in owner_states
    assert visitor_states == FEED_VISIBLE_POST_STATES
    assert PostState.processing not in visitor_states

    # Owner list ignores status filter (except draft) and always returns the visible set.
    assert svc._query_states_for_list(PostState.processing, is_owner=True) == OWNER_VISIBLE_POST_STATES
    assert svc._query_states_for_list(PostState.flagged, is_owner=True) == OWNER_VISIBLE_POST_STATES
    assert svc._query_states_for_list(PostState.draft, is_owner=True) == PostState.draft
