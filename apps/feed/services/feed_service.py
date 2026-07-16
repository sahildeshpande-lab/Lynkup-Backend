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
from apps.engagement.repositories import fetch_post_engagement_flags
from apps.engagement.services.post_reaction_formatters import load_latest_post_reactions
from apps.engagement.services.reaction_service import format_user_reaction
from apps.feed.services.post_service import format_post_detail
from core.images.config import generate_profile_image_url


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
        is_reposted = item["is_reposted"]

        formatted = format_post_detail(
            post,
            author_profile=author_profile,
            is_liked=engagement_flags.user_reaction_for(post.id) is not None,
            is_reposted=is_reposted,  # Now represents: Is this event item a repost?
            is_bookmarked=post.id in engagement_flags.bookmarked_post_ids,
            user_reaction=format_user_reaction(engagement_flags.user_reaction_for(post.id)),
            reactions=latest_reactions.get(post.id),
        )

        # Inject original author profilePhoto_url for backward compatibility
        if author_profile:
            p_url = (
                generate_profile_image_url(author_profile.profile_photo_url)
                if author_profile.profile_photo_url
                else None
            )
            formatted["profilePhoto_url"] = p_url

        if is_reposted and item["reposted_by_profile"]:
            rp = item["reposted_by_profile"]
            rp_photo = (
                generate_profile_image_url(rp.profile_photo_url)
                if rp.profile_photo_url
                else None
            )
            formatted["reposted_by"] = {
                "user_id": rp.user_id,
                "first_name": rp.first_name,
                "last_name": rp.last_name,
                "profilePhoto_url": rp_photo,
                "profile_photo_url": rp_photo,
                "reposted_at": item["reposted_at"]
            }
        else:
            formatted["reposted_by"] = None

        formatted_posts.append(formatted)

    if include_total:
        return formatted_posts, total_items
    return formatted_posts
