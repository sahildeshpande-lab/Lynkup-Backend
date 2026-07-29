from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.engagement.repositories.bookmark_repository import (
    create_bookmark,
    delete_bookmark,
    get_user_bookmark,
)
from apps.engagement.schemas import BookmarkData, BookmarkRequest, BookmarkResponse
from apps.feed.db_models import Post
from common.responses import error_response, success_response

logger = logging.getLogger(__name__)


async def update_bookmark(
    db: AsyncSession,
    user_id: UUID,
    payload: BookmarkRequest,
) -> BookmarkResponse:
    post = (
        await db.execute(select(Post).where(Post.id == payload.post_id))
    ).scalar_one_or_none()
    if post is None:
        return error_response("Post does not exist", response_cls=BookmarkResponse)

    author_id = getattr(post, "author_user_id", None)
    if author_id is not None:
        from common.user_visibility import check_post_engagement_allowed
        await check_post_engagement_allowed(db, user_id, author_id)

    existing = await get_user_bookmark(db, user_id, payload.post_id)

    if payload.is_bookmarked:
        if existing is not None:
            return success_response(
                "Bookmark updated successfully",
                BookmarkData(post_id=payload.post_id, is_bookmarked=True),
                response_cls=BookmarkResponse,
            )
        try:
            await create_bookmark(db, user_id, payload.post_id)
            await db.commit()
            from apps.recommendation.services.engagement_keyword_service import (
                apply_engagement_keyword_update_best_effort,
            )

            await apply_engagement_keyword_update_best_effort(
                db,
                user_id,
                payload.post_id,
                "bookmark",
                added=True,
            )
        except IntegrityError:
            await db.rollback()
            logger.warning(
                "Duplicate bookmark race for user_id=%s post_id=%s",
                user_id,
                payload.post_id,
            )
        except Exception:
            await db.rollback()
            logger.exception(
                "Failed to create bookmark user_id=%s post_id=%s",
                user_id,
                payload.post_id,
            )
            return error_response(
                "Failed to update bookmark",
                response_cls=BookmarkResponse,
            )
    else:
        if existing is None:
            return success_response(
                "Bookmark updated successfully",
                BookmarkData(post_id=payload.post_id, is_bookmarked=False),
                response_cls=BookmarkResponse,
            )
        try:
            await delete_bookmark(db, existing)
            await db.commit()
            from apps.recommendation.services.engagement_keyword_service import (
                apply_engagement_keyword_update_best_effort,
            )

            await apply_engagement_keyword_update_best_effort(
                db,
                user_id,
                payload.post_id,
                "bookmark",
                added=False,
            )
        except Exception:
            await db.rollback()
            logger.exception(
                "Failed to remove bookmark user_id=%s post_id=%s",
                user_id,
                payload.post_id,
            )
            return error_response(
                "Failed to update bookmark",
                response_cls=BookmarkResponse,
            )

    return success_response(
        "Bookmark updated successfully",
        BookmarkData(post_id=payload.post_id, is_bookmarked=payload.is_bookmarked),
        response_cls=BookmarkResponse,
    )
