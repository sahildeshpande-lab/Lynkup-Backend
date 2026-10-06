from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.feed.services import post_service as svc
from common.enums import PostState, ReportEntityType


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
            "apps.notifications.services.notify_post_author",
            AsyncMock(),
        ),
        patch(
            "apps.report.repositories.report_repository.clear_entity_report_queue_counts",
            AsyncMock(),
        ) as clear_counts,
        patch(
            "apps.profiles.services.profile_stats_service.recalculate_posts_count_for_user",
            AsyncMock(),
        ) as recalc,
        patch(
            "apps.profiles.services.profile_stats_service.recalculate_reposter_posts_counts",
            AsyncMock(),
        ),
    ):
        result = await svc.admin_publish_post_service(post.id, "flagged", admin_id, db)

    assert result.state == PostState.flagged
    clear_counts.assert_awaited_once_with(
        db,
        entity_type=ReportEntityType.post,
        entity_id=post.id,
    )
    recalc.assert_awaited_once_with(db, post.author_user_id)


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
            "apps.moderation.services.record_moderation_history",
            AsyncMock(),
        ),
        patch(
            "apps.report.repositories.report_repository.clear_entity_report_queue_counts",
            AsyncMock(),
        ),
        patch(
            "apps.profiles.services.profile_stats_service.recalculate_posts_count_for_user",
            AsyncMock(),
        ) as recalc,
        patch(
            "apps.profiles.services.profile_stats_service.recalculate_reposter_posts_counts",
            AsyncMock(),
        ),
    ):
        result = await svc.admin_publish_post_service(post.id, "published", admin_id, db)

    assert result.state == PostState.published
    recalc.assert_awaited_once_with(db, post.author_user_id)


@pytest.mark.asyncio
async def test_delete_post_service_decrements_posts_count_for_published(mock_db):
    post = _post(state=PostState.published)
    db = mock_db()

    post_result = MagicMock()
    post_result.scalar_one_or_none.return_value = post
    db.execute = AsyncMock(return_value=post_result)

    with (
        patch.object(svc, "_soft_delete_post", AsyncMock(return_value=post.id)) as soft_del,
        patch(
            "apps.profiles.services.profile_stats_service.recalculate_posts_count_for_user",
            AsyncMock(),
        ) as recalc,
    ):
        result = await svc.delete_post_service(post.id, post.author_user_id, db)

    assert result == {"id": post.id, "deleted": True, "state": PostState.deleted.value}
    soft_del.assert_awaited_once()
    recalc.assert_awaited_once_with(db, post.author_user_id)
    assert db.commit.await_count == 2


@pytest.mark.asyncio
async def test_delete_post_service_rejects_draft_posts(mock_db):
    post = _post(state=PostState.draft)
    db = mock_db()

    post_result = MagicMock()
    post_result.scalar_one_or_none.return_value = post
    db.execute = AsyncMock(return_value=post_result)

    with pytest.raises(Exception) as exc_info:
        await svc.delete_post_service(post.id, post.author_user_id, db)

    assert "draft delete endpoint" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_delete_post_service_recalculates_posts_count_for_processing(mock_db):
    post = _post(state=PostState.processing)
    db = mock_db()

    post_result = MagicMock()
    post_result.scalar_one_or_none.return_value = post
    db.execute = AsyncMock(return_value=post_result)

    with (
        patch.object(svc, "_soft_delete_post", AsyncMock(return_value=post.id)),
        patch(
            "apps.profiles.services.profile_stats_service.recalculate_posts_count_for_user",
            AsyncMock(),
        ) as recalc,
    ):
        result = await svc.delete_post_service(post.id, post.author_user_id, db)

    assert result == {"id": post.id, "deleted": True, "state": PostState.deleted.value}
    recalc.assert_awaited_once_with(db, post.author_user_id)
    assert db.commit.await_count == 2


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
async def test_admin_reject_soft_deletes_post(mock_db):
    post = _post(state=PostState.published)
    admin_id = uuid.uuid4()
    db = mock_db()

    post_result = MagicMock()
    post_result.scalar_one_or_none.return_value = post
    db.execute = AsyncMock(return_value=post_result)

    with (
        patch.object(svc, "_soft_delete_post", AsyncMock(return_value=post.id)),
        patch(
            "apps.moderation.services.record_moderation_history",
            AsyncMock(),
        ),
        patch(
            "apps.report.repositories.report_repository.clear_entity_report_queue_counts",
            AsyncMock(),
        ) as clear_counts,
        patch(
            "apps.profiles.services.profile_stats_service.recalculate_posts_count_for_user",
            AsyncMock(),
        ) as recalc,
    ):
        result = await svc.admin_publish_post_service(
            post.id, "rejected", admin_id, db
        )

    assert result == {
        "id": post.id,
        "deleted": True,
        "status": "rejected",
        "state": PostState.rejected.value,
    }
    clear_counts.assert_awaited_once_with(
        db,
        entity_type=ReportEntityType.post,
        entity_id=post.id,
    )
    recalc.assert_awaited_once_with(db, post.author_user_id)

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
            "apps.moderation.services.record_moderation_history",
            AsyncMock(),
        ),
        patch(
            "apps.report.repositories.report_repository.clear_entity_report_queue_counts",
            AsyncMock(),
        ),
        patch(
            "apps.profiles.services.profile_stats_service.recalculate_posts_count_for_user",
            AsyncMock(),
        ) as recalc,
        patch(
            "apps.profiles.services.profile_stats_service.recalculate_reposter_posts_counts",
            AsyncMock(),
        ),
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
    recalc.assert_awaited_once_with(db, post.author_user_id)


@pytest.mark.asyncio
async def test_admin_escalate_allows_optional_notes(mock_db):
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
            "apps.moderation.services.record_moderation_history",
            AsyncMock(),
        ),
        patch(
            "apps.report.repositories.report_repository.clear_entity_report_queue_counts",
            AsyncMock(),
        ),
        patch(
            "apps.profiles.services.profile_stats_service.recalculate_posts_count_for_user",
            AsyncMock(),
        ) as recalc,
        patch(
            "apps.profiles.services.profile_stats_service.recalculate_reposter_posts_counts",
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
    assert result.moderator_id == superadmin_id
    assert result.moderation_notes is None
    recalc.assert_awaited_once_with(db, post.author_user_id)


@pytest.mark.asyncio
async def test_admin_flag_decrements_reposter_posts_count(mock_db):
    post = _post(state=PostState.published)
    admin_id = uuid.uuid4()
    reposter_id = uuid.uuid4()
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
            "apps.notifications.services.notify_post_author",
            AsyncMock(),
        ),
        patch(
            "apps.report.repositories.report_repository.clear_entity_report_queue_counts",
            AsyncMock(),
        ),
        patch(
            "apps.engagement.repositories.repost_repository.list_active_reposter_user_ids",
            AsyncMock(return_value=[reposter_id]),
        ),
        patch(
            "apps.profiles.services.profile_stats_service.recalculate_posts_count_for_user",
            AsyncMock(),
        ) as recalc,
        patch(
            "apps.profiles.services.profile_stats_service.recalculate_reposter_posts_counts",
            AsyncMock(),
        ) as recalc_reposters,
    ):
        result = await svc.admin_publish_post_service(post.id, "flagged", admin_id, db)

    assert result.state == PostState.flagged
    recalc.assert_awaited_once_with(db, post.author_user_id)
    recalc_reposters.assert_awaited_once_with(
        db, post.id, exclude_user_id=post.author_user_id
    )


@pytest.mark.asyncio
async def test_admin_publish_from_flagged_increments_reposter_posts_count(mock_db):
    post = _post(state=PostState.flagged)
    admin_id = uuid.uuid4()
    reposter_id = uuid.uuid4()
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
            "apps.report.repositories.report_repository.clear_entity_report_queue_counts",
            AsyncMock(),
        ),
        patch(
            "apps.engagement.repositories.repost_repository.list_active_reposter_user_ids",
            AsyncMock(return_value=[reposter_id]),
        ),
        patch(
            "apps.profiles.services.profile_stats_service.recalculate_posts_count_for_user",
            AsyncMock(),
        ) as recalc,
        patch(
            "apps.profiles.services.profile_stats_service.recalculate_reposter_posts_counts",
            AsyncMock(),
        ) as recalc_reposters,
    ):
        result = await svc.admin_publish_post_service(post.id, "published", admin_id, db)

    assert result.state == PostState.published
    recalc.assert_awaited_once_with(db, post.author_user_id)
    recalc_reposters.assert_awaited_once_with(
        db, post.id, exclude_user_id=post.author_user_id
    )

