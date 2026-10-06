from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from apps.feed.repositories.post_repository import UserTimelineEvent
from apps.feed.services import post_service as ps
from apps.engagement.repositories.engagement_repository import PostEngagementFlags
from apps.engagement.repositories.feed_user_state import FeedUserState
from apps.feed.services.feed_enrichment_user_state import FeedEnrichmentAndUserState
from apps.feed.services.profile_enrichment import FeedProfileEnrichment
from common.enums import PostState


@contextmanager
def _patch_list_user_posts_timeline(
    *,
    total: int = 0,
    summary: dict | None = None,
):
    mock_summary = summary or {
        "published": 0,
        "flagged": 0,
        "rejected": 0,
        "reinstate": 0,
    }
    with (
        patch(
            "apps.feed.repositories.post_repository.user_exists",
            AsyncMock(return_value=True),
        ),
        patch(
            "apps.feed.repositories.post_repository.count_user_posts_timeline",
            AsyncMock(return_value=total),
        ) as mock_count,
        patch(
            "apps.feed.repositories.post_repository.count_user_posts_summary_by_state",
            AsyncMock(return_value=mock_summary),
        ) as mock_summary_count,
        patch(
            "apps.feed.repositories.post_repository.fetch_user_posts_timeline_events",
            AsyncMock(return_value=[]),
        ) as mock_fetch_events,
        patch(
            "apps.feed.repositories.post_repository.hydrate_user_posts_timeline",
            AsyncMock(return_value=[]),
        ) as mock_hydrate,
        patch(
            "apps.feed.repositories.post_revision_repository.posts_with_triggered_moderation_review",
            AsyncMock(return_value=set()),
        ),
        patch(
            "apps.feed.services.feed_enrichment_user_state.load_feed_enrichment_and_user_state",
            AsyncMock(
                return_value=FeedEnrichmentAndUserState(
                    enrichment=FeedProfileEnrichment(
                        profile_details={},
                        requested_user_ids=set(),
                        connected_user_ids=set(),
                    ),
                    user_state=FeedUserState(
                        engagement=PostEngagementFlags.empty(),
                        latest_reactions={},
                    ),
                    triggered_moderation_post_ids=frozenset(),
                )
            ),
        ),
    ):
        yield mock_count, mock_summary_count, mock_fetch_events, mock_hydrate


@pytest.mark.asyncio
async def test_list_user_posts_items_service_skips_summary_for_regular_user(mock_db) -> None:
    db = mock_db()
    user_id = uuid4()
    current_user = SimpleNamespace(id=user_id, role="user", roles=[])

    with _patch_list_user_posts_timeline(total=3) as (
        _mock_count,
        mock_summary_count,
        _mock_fetch_events,
        _mock_hydrate,
    ):
        items, total, summary = await ps.list_user_posts_items_service(
            current_user=current_user,
            db=db,
            state=None,
        )

    assert items == []
    assert total == 0
    assert summary == ps._empty_user_posts_summary()
    mock_summary_count.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_user_posts_items_service_includes_summary_for_staff(mock_db) -> None:
    db = mock_db()
    user_id = uuid4()
    current_user = SimpleNamespace(id=user_id, role="moderator", roles=[])
    mock_summary = {
        "published": 3,
        "flagged": 2,
        "rejected": 1,
        "reinstate": 1,
    }

    with _patch_list_user_posts_timeline(total=3, summary=mock_summary) as (
        mock_count,
        mock_summary_count,
        mock_fetch_events,
        _mock_hydrate,
    ):
        items, total, summary = await ps.list_user_posts_items_service(
            current_user=current_user,
            db=db,
            state=None,
            page=1,
            page_size=20,
            include_total=True,
        )

    from common.enums import OWNER_VISIBLE_POST_STATES

    assert items == []
    assert total == 3
    assert summary == mock_summary
    mock_summary_count.assert_awaited_once_with(db, user_id=user_id)
    mock_count.assert_awaited_once()
    assert mock_count.await_args.kwargs["state"] == OWNER_VISIBLE_POST_STATES
    assert mock_count.await_args.kwargs["include_reposts"] is True
    assert mock_fetch_events.await_args.kwargs["state"] == OWNER_VISIBLE_POST_STATES
    assert mock_fetch_events.await_args.kwargs["include_reposts"] is True


@pytest.mark.asyncio
async def test_list_user_posts_state_published_is_feed_visible(mock_db) -> None:
    db = mock_db()
    user_id = uuid4()
    current_user = SimpleNamespace(id=user_id, role="user", roles=[])

    with _patch_list_user_posts_timeline(
        total=2,
        summary={
            "published": 2,
            "flagged": 1,
            "rejected": 0,
            "reinstate": 0,
        },
    ) as (mock_count, _summary, mock_fetch_events, _hydrate):
        await ps.list_user_posts_items_service(
            current_user=current_user,
            db=db,
            state="published",
            page=1,
            page_size=10,
        )

    from common.enums import FEED_VISIBLE_POST_STATES

    mock_count.assert_not_awaited()
    assert mock_fetch_events.await_args.kwargs["state"] == FEED_VISIBLE_POST_STATES
    assert mock_fetch_events.await_args.kwargs["include_reposts"] is True
    assert mock_fetch_events.await_args.kwargs["limit"] == 10
    assert mock_fetch_events.await_args.kwargs["offset"] == 0


@pytest.mark.asyncio
async def test_list_user_posts_state_flagged_includes_processing(mock_db) -> None:
    db = mock_db()
    user_id = uuid4()
    current_user = SimpleNamespace(id=user_id, role="user", roles=[])

    with _patch_list_user_posts_timeline(
        total=1,
        summary={
            "published": 0,
            "flagged": 1,
            "rejected": 0,
            "reinstate": 0,
        },
    ) as (mock_count, _summary, mock_fetch_events, _hydrate):
        await ps.list_user_posts_items_service(
            current_user=current_user,
            db=db,
            state="flagged",
            page=1,
            page_size=10,
        )

    expected = (PostState.flagged, PostState.processing)
    mock_count.assert_not_awaited()
    assert mock_fetch_events.await_args.kwargs["state"] == expected
    assert mock_fetch_events.await_args.kwargs["include_reposts"] is False


@pytest.mark.asyncio
async def test_list_user_posts_rejected_forbidden_for_regular_user(mock_db) -> None:
    from fastapi import HTTPException

    db = mock_db()
    current_user = SimpleNamespace(id=uuid4(), role="user", roles=[])

    with pytest.raises(HTTPException) as exc:
        await ps.list_user_posts_items_service(
            current_user=current_user,
            db=db,
            state="rejected",
            page=1,
            page_size=10,
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_list_user_posts_state_rejected_is_staff_only(mock_db) -> None:
    db = mock_db()
    user_id = uuid4()
    current_user = SimpleNamespace(id=user_id, role="moderator", roles=[])

    with _patch_list_user_posts_timeline(
        total=1,
        summary={
            "published": 0,
            "flagged": 0,
            "rejected": 1,
            "reinstate": 0,
        },
    ) as (mock_count, _summary, mock_fetch_events, _hydrate):
        await ps.list_user_posts_items_service(
            current_user=current_user,
            db=db,
            state="rejected",
        )

    mock_count.assert_not_awaited()
    assert mock_fetch_events.await_args.kwargs["state"] == PostState.rejected
    assert mock_fetch_events.await_args.kwargs["include_reposts"] is False


@pytest.mark.asyncio
async def test_list_user_posts_hides_moderation_summary_from_visitors(mock_db) -> None:
    db = mock_db()
    viewer_id = uuid4()
    author_id = uuid4()
    current_user = SimpleNamespace(id=viewer_id, role="user", roles=[])

    with _patch_list_user_posts_timeline(total=4) as (
        _mock_count,
        mock_summary_count,
        _mock_fetch_events,
        _mock_hydrate,
    ):
        _items, _total, summary = await ps.list_user_posts_items_service(
            current_user=current_user,
            db=db,
            target_user_id=author_id,
            state="published",
        )

    mock_summary_count.assert_not_awaited()
    assert summary == ps._empty_user_posts_summary()


@pytest.mark.asyncio
async def test_list_user_posts_timeline_respects_optional_pagination(mock_db) -> None:
    db = mock_db()
    user_id = uuid4()
    current_user = SimpleNamespace(id=user_id, role="user", roles=[])

    with _patch_list_user_posts_timeline(total=50) as (
        _count,
        _summary,
        mock_fetch_events,
        _hydrate,
    ):
        await ps.list_user_posts_items_service(
            current_user=current_user,
            db=db,
            state="published",
            page=2,
            page_size=10,
        )

    assert mock_fetch_events.await_args.kwargs["offset"] == 10
    assert mock_fetch_events.await_args.kwargs["limit"] == 10

    with _patch_list_user_posts_timeline(total=50) as (
        _count,
        _summary,
        mock_fetch_events,
        _hydrate,
    ):
        await ps.list_user_posts_items_service(
            current_user=current_user,
            db=db,
            state="published",
        )

    assert mock_fetch_events.await_args.kwargs["offset"] == 0
    assert mock_fetch_events.await_args.kwargs["limit"] is None


@pytest.mark.asyncio
async def test_list_user_posts_paginated_skips_count_by_default(mock_db) -> None:
    db = mock_db()
    user_id = uuid4()
    current_user = SimpleNamespace(id=user_id, role="user", roles=[])

    with _patch_list_user_posts_timeline(total=99) as (
        mock_count,
        _summary,
        _mock_fetch_events,
        _mock_hydrate,
    ):
        _items, total, _summary = await ps.list_user_posts_items_service(
            current_user=current_user,
            db=db,
            page=1,
            page_size=10,
        )

    mock_count.assert_not_awaited()
    assert total == 0


@pytest.mark.asyncio
async def test_list_user_posts_paginated_fetches_exact_page_size(mock_db) -> None:
    """Offset pagination fetches page_size rows (no +1 peek for cursor)."""
    db = mock_db()
    user_id = uuid4()
    current_user = SimpleNamespace(id=user_id, role="user", roles=[])
    events = [
        UserTimelineEvent(
            post_id=uuid4(),
            repost_id=None,
            event_type="post",
            sort_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        for _ in range(10)
    ]

    with _patch_list_user_posts_timeline() as (
        mock_count,
        _summary,
        mock_fetch_events,
        mock_hydrate,
    ):
        mock_fetch_events.return_value = events
        items, total, _summary = await ps.list_user_posts_items_service(
            current_user=current_user,
            db=db,
            page=1,
            page_size=10,
        )

    mock_count.assert_not_awaited()
    assert len(items) == 0
    assert total == 0
    assert mock_fetch_events.await_args.kwargs["limit"] == 10
    mock_hydrate.assert_awaited_once()
    assert len(mock_hydrate.await_args.args[1]) == 10


@pytest.mark.asyncio
async def test_count_user_posts_summary_by_state_rollups(mock_db, scalar_result) -> None:
    from apps.feed.repositories import post_repository as repo

    user_id = uuid4()
    db = mock_db(
        scalar_result(
            values=[
                (PostState.published, 2),
                (PostState.reinstate, 1),
                (PostState.flagged, 1),
                (PostState.processing, 1),
                (PostState.rejected, 1),
            ]
        )
    )

    summary = await repo.count_user_posts_summary_by_state(db, user_id=user_id)

    assert summary == {
        "published": 3,
        "flagged": 2,
        "rejected": 1,
        "reinstate": 1,
    }
