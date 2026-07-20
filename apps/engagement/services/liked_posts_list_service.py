from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from apps.connections.services.recommendation_service import get_user_connections
from apps.engagement.repositories.engagement_repository import fetch_post_engagement_flags
from apps.engagement.repositories.liked_posts_repository import (
    count_user_liked_posts,
    fetch_user_liked_posts,
)
from apps.engagement.schemas import LikedPostsListData, LikedPostsListResponse
from apps.engagement.services.post_reaction_formatters import load_latest_post_reactions
from apps.engagement.services.reaction_service import format_user_reaction
from apps.feed.services.profile_enrichment import (
    load_profile_details,
    load_requested_user_ids,
)
from apps.feed.services.post_service import format_post_detail
from common.pagination import build_paginated_response
from common.responses import success_response


async def list_liked_posts(
    db: AsyncSession,
    user_id: UUID,
    *,
    page: int | None = None,
    page_size: int | None = None,
) -> LikedPostsListResponse:
    async def _format_rows(rows):
        post_ids = [post.id for post, *_ in rows]
        engagement_flags = await fetch_post_engagement_flags(db, user_id, post_ids)
        latest_reactions = await load_latest_post_reactions(db, post_ids, per_type_limit=3)

        profiles_by_user_id = {
            post.author_user_id: author_profile
            for post, author_profile, *_ in rows
            if author_profile is not None
        }
        connection_ids = await get_user_connections(db, user_id)
        profile_details = await load_profile_details(db, profiles_by_user_id)
        requested_user_ids = await load_requested_user_ids(
            db,
            user_id,
            set(profiles_by_user_id),
        )

        return [
            format_post_detail(
                post,
                author_profile=author_profile,
                moderator_user=mod_user,
                moderator_profile=mod_profile,
                is_connected=post.author_user_id in connection_ids,
                is_requested=post.author_user_id in requested_user_ids,
                profile_details=profile_details.get(post.author_user_id),
                is_liked=engagement_flags.user_reaction_for(post.id) is not None,
                is_reposted=post.id in engagement_flags.reposted_post_ids,
                is_bookmarked=post.id in engagement_flags.bookmarked_post_ids,
                user_reaction=format_user_reaction(engagement_flags.user_reaction_for(post.id)),
                reactions=latest_reactions.get(post.id),
                viewer_user_id=user_id,
            )
            for post, author_profile, mod_user, mod_profile in rows
        ]

    if page is not None and page_size is not None:
        total_items = await count_user_liked_posts(db, user_id)
        rows = await fetch_user_liked_posts(
            db,
            user_id,
            offset=(page - 1) * page_size,
            limit=page_size,
        )
        items = await _format_rows(rows)
        data = build_paginated_response(items, page, page_size, total_items).model_dump()
    else:
        rows = await fetch_user_liked_posts(db, user_id, offset=0, limit=None)
        items = await _format_rows(rows)
        data = {
            "items": items,
            "page": 1,
            "pageSize": len(items),
            "totalItems": len(items),
            "totalPages": 1 if items else 0,
        }

    message = "Liked posts fetched successfully" if items else "No liked posts found"
    return success_response(
        message,
        LikedPostsListData(**data),
        response_cls=LikedPostsListResponse,
    )
