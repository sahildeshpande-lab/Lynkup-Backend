from __future__ import annotations

import logging
import time
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from apps.feed.repositories.feed_repository import (
    count_feed_posts,
    fetch_feed_posts,
)
from apps.engagement.services.reaction_service import format_user_reaction
from apps.feed.services.feed_enrichment_user_state import (
    load_feed_enrichment_and_user_state as _load_feed_enrichment_and_user_state,
)
from apps.feed.services.post_service import format_post_detail, format_repost_item

logger = logging.getLogger(__name__)


def _perf_ms(started_at: float) -> float:
    return (time.perf_counter() - started_at) * 1000.0


async def get_feed_service(
    current_user_id: UUID,
    db: AsyncSession,
    page: int | None = None,
    page_size: int | None = None,
    cursor: str | None = None,
    include_total: bool = False,
) -> list[dict] | tuple[list[dict], int, str | None]:
    """
    Return feed events (posts and reposts).

    Include authors/reposters matching the viewer's university, major, or minor,
    plus posts from connected users even when their academics differ. Rank by
    engagement. If none match, fall back to all visible posts.

    Pagination uses keyset (cursor) internally. Legacy page/pageSize remain supported
    without changing the JSON response contract. When include_total=True, also returns
    next_cursor (opaque, or None on the last page).
    """
    service_started = time.perf_counter()
    # Viewer academics / visibility are evaluated inside feed SQL via
    # ``LEFT JOIN profiles me``. Do not load a discarded ORM Profile here.
    # Ranking/visibility use SQL EXISTS for connections — Python connection IDs
    # are formatting-only (is_connected) and come from enrichment.
    empty_connection_ids: set[UUID] = set()

    # Phase 7: only pay for count when the response needs totalItems.
    total_items = 0
    count_ms = 0.0
    if include_total:
        count_started = time.perf_counter()
        total_items = await count_feed_posts(
            db, current_user_id, None, empty_connection_ids
        )
        count_ms = _perf_ms(count_started)

    if page is None and page_size is None and cursor is None:
        fetch_cursor = None
        limit = None
        slice_start = None
    elif cursor is not None:
        # Cursor path: ignore page offset; pageSize still caps the page.
        fetch_cursor = cursor
        limit = page_size or 20
        slice_start = None
    else:
        p = page or 1
        ps = page_size or 20
        if p == 1:
            fetch_cursor = None
            limit = ps
            slice_start = None
        else:
            # No SQL OFFSET: fetch through the requested page, then slice.
            fetch_cursor = None
            limit = p * ps
            slice_start = (p - 1) * ps

    events_started = time.perf_counter()
    raw_items, next_cursor = await fetch_feed_posts(
        db,
        current_user_id,
        None,
        empty_connection_ids,
        cursor=fetch_cursor,
        limit=limit,
    )
    events_ms = _perf_ms(events_started)

    if slice_start is not None:
        ps = page_size or 20
        raw_items = raw_items[slice_start : slice_start + ps]
        next_cursor = None

    if not raw_items:
        results = []
        logger.info(
            "[FEED_PERF] feed_service_total=%.2fms count=%.2fms events=%.2fms "
            "items=0 include_total=%s sql_statements=%s",
            _perf_ms(service_started),
            count_ms,
            events_ms,
            include_total,
            (1 if include_total else 0) + 1,  # count? + events (no hydration)
        )
        if include_total:
            return results, total_items, next_cursor
        return results

    # Normalize raw items for compatibility with old test mock formats
    feed_items = []
    for item in raw_items:
        if isinstance(item, dict):
            feed_items.append(item)
        elif isinstance(item, tuple):
            if len(item) == 2:
                post, profile = item
                feed_items.append({
                    "post": post,
                    "author_profile": profile,
                    "is_reposted": False,
                    "repost_id": None,
                    "reposted_by_profile": None,
                    "reposted_at": None,
                })
            elif len(item) == 5:
                feed_type, activity_id, post, reposter_profile, activity_created_at = item
                feed_items.append({
                    "post": post,
                    "author_profile": getattr(post, "_author_profile", None),
                    "is_reposted": (feed_type == "repost"),
                    "repost_id": activity_id if feed_type == "repost" else None,
                    "reposted_by_profile": reposter_profile,
                    "reposted_at": activity_created_at,
                })

    post_ids = [item["post"].id for item in feed_items]
    profiles_by_user_id = {}
    for item in feed_items:
        if item.get("author_profile") is not None:
            profiles_by_user_id[item["post"].author_user_id] = item["author_profile"]
        reposter_profile = item.get("reposted_by_profile")
        reposter_user_id = getattr(reposter_profile, "user_id", None)
        if reposter_profile is not None and reposter_user_id is not None:
            profiles_by_user_id[reposter_user_id] = reposter_profile

    # Phase 7 STEP4: enrichment + user-state in one SQL round trip.
    enrich_state_started = time.perf_counter()
    combined = await _load_feed_enrichment_and_user_state(
        db,
        current_user_id,
        profiles_by_user_id,
        post_ids,
        per_type_limit=3,
    )
    enrich_state_ms = _perf_ms(enrich_state_started)
    enrichment = combined.enrichment
    user_state = combined.user_state
    profile_details = enrichment.profile_details
    requested_user_ids = enrichment.requested_user_ids
    connection_ids = enrichment.connected_user_ids
    engagement_flags = user_state.engagement
    latest_reactions = user_state.latest_reactions

    format_started = time.perf_counter()
    formatted_posts = []
    for item in feed_items:
        post = item["post"]
        author_profile = item["author_profile"]
        is_repost_event = item["is_reposted"]

        is_liked = engagement_flags.user_reaction_for(post.id) is not None
        is_bookmarked = post.id in engagement_flags.bookmarked_post_ids
        viewer_has_reposted = post.id in engagement_flags.reposted_post_ids
        user_reaction = format_user_reaction(engagement_flags.user_reaction_for(post.id))
        reactions = latest_reactions.get(post.id)

        if is_repost_event and item.get("reposted_by_profile") and item.get("repost_id"):
            formatted = format_repost_item(
                post,
                original_author_profile=author_profile,
                reposter_profile=item["reposted_by_profile"],
                repost_id=item["repost_id"],
                reposted_at=item["reposted_at"],
                original_author_is_connected=post.author_user_id in connection_ids,
                original_author_is_requested=post.author_user_id in requested_user_ids,
                reposter_is_connected=(
                    item["reposted_by_profile"].user_id in connection_ids
                ),
                reposter_is_requested=(
                    item["reposted_by_profile"].user_id in requested_user_ids
                ),
                original_author_details=profile_details.get(post.author_user_id),
                reposter_details=profile_details.get(
                    item["reposted_by_profile"].user_id
                ),
                is_liked=is_liked,
                viewer_has_reposted=viewer_has_reposted,
                is_bookmarked=is_bookmarked,
                user_reaction=user_reaction,
                reactions=reactions,
                viewer_user_id=current_user_id,
            )
        else:
            formatted = format_post_detail(
                post,
                author_profile=author_profile,
                is_connected=post.author_user_id in connection_ids,
                is_requested=post.author_user_id in requested_user_ids,
                profile_details=profile_details.get(post.author_user_id),
                is_liked=is_liked,
                is_reposted=viewer_has_reposted,
                is_bookmarked=is_bookmarked,
                user_reaction=user_reaction,
                reactions=reactions,
                reposted_data=None,
                viewer_user_id=current_user_id,
            )

        formatted_posts.append(formatted)

    format_ms = _perf_ms(format_started)
    # count? + events + combined hydration + enrichment_user_state
    sql_statements = (1 if include_total else 0) + 3
    logger.info(
        "[FEED_PERF] feed_service_total=%.2fms count=%.2fms events_hydrate=%.2fms "
        "enrichment_user_state=%.2fms formatting=%.2fms items=%s "
        "include_total=%s sql_statements=%s",
        _perf_ms(service_started),
        count_ms,
        events_ms,
        enrich_state_ms,
        format_ms,
        len(formatted_posts),
        include_total,
        sql_statements,
    )

    if include_total:
        return formatted_posts, total_items, next_cursor
    return formatted_posts
