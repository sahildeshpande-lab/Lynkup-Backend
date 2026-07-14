from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from apps.engagement.repositories.bookmark_repository import (
    count_user_bookmarks,
    fetch_user_bookmarked_posts,
)
from apps.engagement.repositories.engagement_repository import fetch_post_engagement_flags
from apps.engagement.schemas import BookmarkListData, BookmarkListResponse
from apps.engagement.services.reaction_service import format_user_reaction
from apps.feed.services.post_service import format_post_detail
from common.pagination import build_paginated_response
from common.responses import success_response


async def list_bookmarked_posts(
    db: AsyncSession,
    user_id: UUID,
    *,
    page: int | None = None,
    page_size: int | None = None,
) -> BookmarkListResponse:
    async def _format_rows(rows):
        post_ids = [post.id for post, *_ in rows]
        engagement_flags = await fetch_post_engagement_flags(db, user_id, post_ids)
        return [
            format_post_detail(
                post,
                author_profile=author_profile,
                moderator_user=mod_user,
                moderator_profile=mod_profile,
                is_liked=engagement_flags.user_reaction_for(post.id) is not None,
                is_reposted=post.id in engagement_flags.reposted_post_ids,
                is_bookmarked=True,
                user_reaction=format_user_reaction(engagement_flags.user_reaction_for(post.id)),
            )
            for post, author_profile, mod_user, mod_profile in rows
        ]

    if page is not None and page_size is not None:
        total_items = await count_user_bookmarks(db, user_id)
        rows = await fetch_user_bookmarked_posts(
            db,
            user_id,
            offset=(page - 1) * page_size,
            limit=page_size,
        )
        items = await _format_rows(rows)
        data = build_paginated_response(items, page, page_size, total_items).model_dump()
    else:
        rows = await fetch_user_bookmarked_posts(db, user_id, offset=0, limit=None)
        items = await _format_rows(rows)
        data = {
            "items": items,
            "page": 1,
            "pageSize": len(items),
            "totalItems": len(items),
            "totalPages": 1 if items else 0,
        }

    message = "Bookmarked posts fetched successfully" if items else "No bookmarked posts found"
    return success_response(
        message,
        BookmarkListData(**data),
        response_cls=BookmarkListResponse,
    )
