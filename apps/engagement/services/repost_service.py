from __future__ import annotations

import logging
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.engagement.repositories.reaction_repository import get_post_for_update
from apps.engagement.repositories.repost_repository import (
    create_repost,
    delete_repost,
    get_profile_id_for_user,
    get_user_repost,
    update_post_repost_count,
)
from apps.feed.db_models import Post
from apps.engagement.schemas import RepostData, RepostResponse
from apps.profiles.services.profile_stats_service import (
    decrement_posts_count_for_user,
    increment_posts_count_for_user,
)
from common.responses import success_response
from common.enums import PostState

logger = logging.getLogger(__name__)


async def toggle_repost(
    db: AsyncSession,
    user_id: UUID,
    post_id: UUID,
    is_reposted: bool,
) -> RepostResponse:
    # 1. Validate post exists
    post = await get_post_for_update(db, post_id)
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")

    # Validate post is in published state
    if post.state != PostState.published:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Post is not published",
        )

    author_id = getattr(post, "author_user_id", None)
    if author_id is not None:
        from common.user_visibility import check_post_engagement_allowed
        await check_post_engagement_allowed(db, user_id, author_id)

    profile_id = await get_profile_id_for_user(db, user_id)
    if profile_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")

    existing = await get_user_repost(db, profile_id, post_id)

    if is_reposted:
        # Check if repost already exists
        if existing is not None:
            return success_response(
                "Post reposted successfully",
                RepostData(
                    post_id=post_id,
                    is_reposted=True,
                    repost_count=post.repost_count,
                ),
                response_cls=RepostResponse,
            )

        # Create repost record (counts as a fresh post for the reposter)
        try:
            await create_repost(db, profile_id, user_id, post_id)
            repost_count = await update_post_repost_count(db, post_id, 1)
            await increment_posts_count_for_user(db, user_id)
            await db.commit()
        except IntegrityError:
            await db.rollback()
            logger.warning(
                "Duplicate repost race for profile_id=%s post_id=%s",
                profile_id,
                post_id,
            )
            post = await db.get(Post, post_id)
            repost_count = post.repost_count if post else 0
            return success_response(
                "Post reposted successfully",
                RepostData(
                    post_id=post_id,
                    is_reposted=True,
                    repost_count=repost_count,
                ),
                response_cls=RepostResponse,
            )
        except Exception:
            await db.rollback()
            logger.exception(
                "Failed to repost post user_id=%s post_id=%s",
                user_id,
                post_id,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to repost post",
            )

        return success_response(
            "Post reposted successfully",
            RepostData(
                post_id=post_id,
                is_reposted=True,
                repost_count=repost_count,
            ),
            response_cls=RepostResponse,
        )
    else:
        # If not found, return success response with current state
        if existing is None:
            return success_response(
                "Repost removed successfully",
                RepostData(
                    post_id=post_id,
                    is_reposted=False,
                    repost_count=post.repost_count,
                ),
                response_cls=RepostResponse,
            )

        # Hard delete repost record
        try:
            await delete_repost(db, existing)
            repost_count = await update_post_repost_count(db, post_id, -1)
            await decrement_posts_count_for_user(db, user_id)
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception(
                "Failed to remove repost post user_id=%s post_id=%s",
                user_id,
                post_id,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to remove repost",
            )

        return success_response(
            "Repost removed successfully",
            RepostData(
                post_id=post_id,
                is_reposted=False,
                repost_count=repost_count,
            ),
            response_cls=RepostResponse,
        )
