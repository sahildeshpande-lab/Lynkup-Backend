from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.profiles.services import profile_stats_service as svc


@pytest.mark.asyncio
async def test_get_connection_count_for_profile_returns_zero_without_user():
    assert await svc.get_connection_count_for_profile(AsyncMock(), None) == 0


@pytest.mark.asyncio
async def test_get_connection_count_for_profile_uses_query_result():
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one.return_value = 4
    db.execute = AsyncMock(return_value=result)

    count = await svc.get_connection_count_for_profile(db, uuid.uuid4())

    assert count == 4
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_count_public_posts_for_user_sums_authored_and_reposts():
    db = AsyncMock()
    user_id = uuid.uuid4()

    with (
        patch(
            "apps.feed.repositories.post_repository.count_posts_by_state",
            AsyncMock(return_value=2),
        ),
        patch(
            "apps.engagement.repositories.repost_repository.count_active_reposts_for_user",
            AsyncMock(return_value=1),
        ),
    ):
        total = await svc.count_public_posts_for_user(db, user_id)

    assert total == 3


@pytest.mark.asyncio
async def test_recalculate_posts_count_for_user_repairs_orphaned_reposts():
    user_id = uuid.uuid4()
    profile = SimpleNamespace(user_id=user_id, posts_count=5)
    db = AsyncMock()
    profile_result = MagicMock()
    profile_result.scalar_one_or_none.return_value = profile
    db.execute = AsyncMock(return_value=profile_result)

    with (
        patch(
            "apps.engagement.repositories.repost_repository.repair_orphaned_reposts_for_user",
            AsyncMock(),
        ) as repair,
        patch.object(svc, "count_public_posts_for_user", AsyncMock(return_value=3)),
    ):
        await svc.recalculate_posts_count_for_user(db, user_id)

    repair.assert_awaited_once_with(db, user_id)
    assert profile.posts_count == 3


@pytest.mark.asyncio
async def test_adjust_counts_for_deleting_user_zeros_posts_and_deactivates_connections(mock_db):
    deleting_user_id = uuid.uuid4()
    peer_user_id = uuid.uuid4()
    deleting_profile = SimpleNamespace(id=uuid.uuid4(), user_id=deleting_user_id, posts_count=4)
    connection = SimpleNamespace(
        user_low_id=deleting_user_id,
        user_high_id=peer_user_id,
        is_active=True,
    )

    deleting_profile_result = MagicMock()
    deleting_profile_result.scalar_one_or_none.return_value = deleting_profile
    connections_result = MagicMock()
    connections_result.scalars.return_value.all.return_value = [connection]

    db = mock_db()
    db.execute = AsyncMock(
        side_effect=[
            deleting_profile_result,
            connections_result,
        ]
    )

    await svc.adjust_counts_for_deleting_user(db, deleting_user_id)

    assert deleting_profile.posts_count == 0
    assert connection.is_active is False
    db.add.assert_called_with(connection)


@pytest.mark.asyncio
async def test_adjust_reposter_posts_counts_recalculates_each_reposter():
    post_id = uuid.uuid4()
    author_id = uuid.uuid4()
    reposter_id = uuid.uuid4()
    db = AsyncMock()

    with (
        patch(
            "apps.engagement.repositories.repost_repository.list_active_reposter_user_ids",
            AsyncMock(return_value=[author_id, reposter_id]),
        ),
        patch.object(svc, "recalculate_posts_count_for_user", AsyncMock()) as recalc,
    ):
        await svc.adjust_reposter_posts_counts(
            db, post_id, decrement=True, exclude_user_id=author_id
        )

    recalc.assert_awaited_once_with(db, reposter_id)


@pytest.mark.asyncio
async def test_sync_posts_count_for_visibility_change_recalculates_author_and_reposters():
    post_id = uuid.uuid4()
    author_id = uuid.uuid4()
    db = AsyncMock()

    with (
        patch.object(svc, "recalculate_posts_count_for_user", AsyncMock()) as recalc,
        patch.object(svc, "recalculate_reposter_posts_counts", AsyncMock()) as reposters,
    ):
        await svc.sync_posts_count_for_visibility_change(
            db,
            post_id=post_id,
            author_user_id=author_id,
            was_counted=True,
            now_counted=False,
        )

    recalc.assert_awaited_once_with(db, author_id)
    reposters.assert_awaited_once_with(db, post_id, exclude_user_id=author_id)


@pytest.mark.asyncio
async def test_sync_posts_count_for_visibility_change_noop_when_unchanged():
    db = AsyncMock()
    with (
        patch.object(svc, "recalculate_posts_count_for_user", AsyncMock()) as recalc,
        patch.object(svc, "recalculate_reposter_posts_counts", AsyncMock()) as reposters,
    ):
        await svc.sync_posts_count_for_visibility_change(
            db,
            post_id=uuid.uuid4(),
            author_user_id=uuid.uuid4(),
            was_counted=True,
            now_counted=True,
        )

    recalc.assert_not_called()
    reposters.assert_not_called()

