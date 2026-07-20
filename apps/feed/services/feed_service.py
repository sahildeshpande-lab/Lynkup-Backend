from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from apps.connections.services.recommendation_service import get_user_connections
from apps.feed.repositories.feed_repository import (
    count_feed_posts,
    fetch_feed_posts,
    fetch_viewer_profile,
)
from apps.engagement.repositories import fetch_post_engagement_flags
from apps.engagement.services.post_reaction_formatters import load_latest_post_reactions
from apps.engagement.services.reaction_service import format_user_reaction
from apps.feed.services.post_service import format_post_detail, format_repost_item
from apps.feed.services.profile_enrichment import (
    load_profile_details as _load_profile_details,
    load_requested_user_ids as _load_requested_user_ids,
)


async def get_feed_service(
    current_user_id: UUID,
    db: AsyncSession,
    page: int | None = None,
    page_size: int | None = None,
    include_total: bool = False,
) -> list[dict] | tuple[list[dict], int]:
    """
    Return feed events (posts and reposts) ordered by event creation timestamp.
    """
    viewer_profile = await fetch_viewer_profile(db, current_user_id)
    connection_ids = await get_user_connections(db, current_user_id)
    total_items = await count_feed_posts(db, current_user_id, viewer_profile, connection_ids)

    if page is None and page_size is None:
        offset = 0
        limit = None
    else:
        p = page or 1
        ps = page_size or 20
        offset = (p - 1) * ps
        limit = ps

    raw_items = await fetch_feed_posts(
        db,
        current_user_id,
        viewer_profile,
        connection_ids,
        offset=offset,
        limit=limit,
    )

    if not raw_items:
        results = []
        if include_total:
            return results, total_items
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

    profile_details = await _load_profile_details(db, profiles_by_user_id)
    requested_user_ids = await _load_requested_user_ids(
        db,
        current_user_id,
        set(profiles_by_user_id),
    )
    engagement_flags = await fetch_post_engagement_flags(
        db,
        current_user_id,
        post_ids,
    )
    latest_reactions = await load_latest_post_reactions(db, post_ids, per_type_limit=3)

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

    if include_total:
        return formatted_posts, total_items
    return formatted_posts

