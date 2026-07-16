from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from apps.connections.services.recommendation_service import get_user_connections
from apps.feed.db_models import Post
from apps.feed.repositories.feed_repository import (
    count_feed_posts,
    fetch_feed_posts,
    fetch_viewer_profile,
)


async def get_feed_service(
    current_user_id: UUID,
    db: AsyncSession,
    page: int | None = None,
    page_size: int | None = None,
    include_total: bool = False,
) -> list[dict] | tuple[list[dict], int]:
    """
    Return feed items (posts and reposts) with profile-visibility filters applied in SQL.
    Ordered by activity timestamp DESC.
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

    rows = await fetch_feed_posts(
        db,
        current_user_id,
        viewer_profile,
        connection_ids,
        offset=offset,
        limit=limit,
    )

    from apps.engagement.repositories import fetch_post_engagement_flags
    from apps.engagement.services.post_reaction_formatters import load_latest_post_reactions
    from apps.engagement.services.reaction_service import format_user_reaction
    from apps.feed.services.post_service import format_post_detail
    from core.images import generate_profile_image_url

    if not rows:
        results = []
        if include_total:
            return results, total_items
        return results

    # Fallback/parsing for rows
    parsed_rows = []
    post_ids = []
    for row in rows:
        if len(row) == 5:
            feed_type, activity_id, post, reposter_profile, activity_created_at = row
        else:
            post, profile = row
            post._author_profile = profile
            feed_type = "post"
            activity_id = post.id
            reposter_profile = None
            activity_created_at = getattr(post, "created_at", None)

        parsed_rows.append((feed_type, activity_id, post, reposter_profile, activity_created_at))
        post_ids.append(post.id)

    # Batch load engagement flags and reactions
    engagement_flags = await fetch_post_engagement_flags(
        db,
        current_user_id,
        post_ids,
    )
    latest_reactions = await load_latest_post_reactions(db, post_ids, per_type_limit=3)

    results = []
    for feed_type, activity_id, post, reposter_profile, activity_created_at in parsed_rows:
        is_liked = engagement_flags.user_reaction_for(post.id) is not None
        is_reposted = post.id in engagement_flags.reposted_post_ids
        is_bookmarked = post.id in engagement_flags.bookmarked_post_ids
        user_reaction = format_user_reaction(engagement_flags.user_reaction_for(post.id))
        reactions = latest_reactions.get(post.id)

        post_data = format_post_detail(
            post,
            author_profile=getattr(post, "_author_profile", None),
            is_liked=is_liked,
            is_reposted=(feed_type == "repost"),
            is_bookmarked=is_bookmarked,
            user_reaction=user_reaction,
            reactions=reactions,
        )

        if feed_type == "repost" and reposter_profile is not None:
            photo_url = (
                generate_profile_image_url(reposter_profile.profile_photo_url)
                if reposter_profile.profile_photo_url
                else None
            )
            post_data["reposted_by"] = {
                "id": reposter_profile.user_id,
                "first_name": reposter_profile.first_name,
                "last_name": reposter_profile.last_name,
                "profilePhoto_url": photo_url,
            }
        else:
            post_data["reposted_by"] = None

        results.append(post_data)

    if include_total:
        return results, total_items
    return results
