from __future__ import annotations

import logging
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.engagement.repositories.reaction_repository import get_post_for_update
from apps.engagement.repositories.repost_repository import get_profile_id_for_user
from apps.engagement.repositories.share_repository import (
    create_share_event,
    get_user_share_event,
    update_post_share_count,
)
from apps.engagement.schemas import ShareResponse
from common.responses import success_response

logger = logging.getLogger(__name__)


async def share_post(
    db: AsyncSession,
    user_id: UUID,
    post_id: UUID,
) -> ShareResponse:
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

    if await get_user_share_event(db, user_id, post_id) is not None:
        return success_response(
            "Post shared successfully",
            response_cls=ShareResponse,
        )

    try:
        await create_share_event(db, user_id, post_id)
        await update_post_share_count(db, post_id, 1)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        logger.warning(
            "Duplicate share race for profile_id=%s user_id=%s post_id=%s",
            profile_id,
            user_id,
            post_id,
        )
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to share post user_id=%s post_id=%s",
            user_id,
            post_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to share post",
        ) from None

    return success_response(
        "Post shared successfully",
        response_cls=ShareResponse,
    )
