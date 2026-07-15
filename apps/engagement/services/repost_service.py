from __future__ import annotations

import logging
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.engagement.repositories.reaction_repository import get_post_for_update
from apps.engagement.repositories.repost_repository import (
    create_repost,
    get_profile_id_for_user,
    get_user_repost,
    update_post_repost_count,
)
from apps.engagement.schemas import RepostData, RepostResponse
from common.responses import error_response, success_response

logger = logging.getLogger(__name__)


async def repost_post(
    db: AsyncSession,
    user_id: UUID,
    post_id: UUID,
) -> RepostResponse:
    post = await get_post_for_update(db, post_id)
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")

    author_id = getattr(post, "author_user_id", None)
    if author_id is not None:
        from common.user_visibility import check_post_engagement_allowed
        await check_post_engagement_allowed(db, user_id, author_id)

    profile_id = await get_profile_id_for_user(db, user_id)
    if profile_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")

    existing = await get_user_repost(db, profile_id, post_id)
    if existing is not None:
        return error_response(
            "You have already reposted this post",
            response_cls=RepostResponse,
        )

    try:
        await create_repost(db, profile_id, post_id)
        repost_count = await update_post_repost_count(db, post_id, 1)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        logger.warning(
            "Duplicate repost race for profile_id=%s post_id=%s",
            profile_id,
            post_id,
        )
        return error_response(
            "You have already reposted this post",
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
