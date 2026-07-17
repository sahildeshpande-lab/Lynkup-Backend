from __future__ import annotations

from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.connections.db_models import ConnectionRequest
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
from apps.feed.services.post_service import format_post_detail, format_repost_item
from apps.profiles.db_models import AcademicInterest, University


async def _load_profile_details(
    db: AsyncSession,
    profiles_by_user_id: dict[UUID, object],
) -> dict[UUID, dict]:
    """Batch-resolve feed profile fields that are stored as foreign-key IDs."""
    university_ids = {
        profile.university_id
        for profile in profiles_by_user_id.values()
        if getattr(profile, "university_id", None) is not None
    }
    profile_interest_ids: dict[UUID, list[int]] = {}
    for user_id, profile in profiles_by_user_id.items():
        normalized_ids = []
        for interest_id in (
            getattr(profile, "profile_interests_id", None) or []
        ):
            try:
                normalized_ids.append(int(interest_id))
            except (TypeError, ValueError):
                continue
        profile_interest_ids[user_id] = normalized_ids
    interest_ids = {
        interest_id
        for normalized_ids in profile_interest_ids.values()
        for interest_id in normalized_ids
    }

    university_names: dict[UUID, str] = {}
    if university_ids:
        rows = (
            await db.execute(
                select(University.id, University.name).where(
                    University.id.in_(university_ids)
                )
            )
        ).all()
        university_names = {row.id: row.name for row in rows}

    interest_names: dict[int, str] = {}
    if interest_ids:
        rows = (
            await db.execute(
                select(AcademicInterest.id, AcademicInterest.name).where(
                    AcademicInterest.id.in_(interest_ids)
                )
            )
        ).all()
        interest_names = {row.id: row.name for row in rows}

    return {
        user_id: {
            "university": university_names.get(
                getattr(profile, "university_id", None)
            ),
            "bio": getattr(profile, "bio", None),
            "academic_interest": [
                interest_names[interest_id]
                for interest_id in profile_interest_ids[user_id]
                if interest_id in interest_names
            ],
            "major": getattr(profile, "major", None),
            "minor": getattr(profile, "minor", None),
        }
        for user_id, profile in profiles_by_user_id.items()
    }


async def _load_requested_user_ids(
    db: AsyncSession,
    current_user_id: UUID,
    target_user_ids: set[UUID],
) -> set[UUID]:
    """Return users with a pending request in either direction with the viewer."""
    if not target_user_ids:
        return set()

    rows = (
        await db.execute(
            select(ConnectionRequest).where(
                ConnectionRequest.status == "pending",
                or_(
                    and_(
                        ConnectionRequest.sender_user_id == current_user_id,
                        ConnectionRequest.receiver_user_id.in_(target_user_ids),
                    ),
                    and_(
                        ConnectionRequest.receiver_user_id == current_user_id,
                        ConnectionRequest.sender_user_id.in_(target_user_ids),
                    ),
                ),
            )
        )
    ).scalars().all()

    requested_ids: set[UUID] = set()
    for request in rows:
        if request.sender_user_id == current_user_id:
            requested_ids.add(request.receiver_user_id)
        else:
            requested_ids.add(request.sender_user_id)
    return requested_ids


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
            )

        formatted_posts.append(formatted)

    if include_total:
        return formatted_posts, total_items
    return formatted_posts

