from __future__ import annotations

import logging
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.engagement.repositories import (
    delete_user_reaction,
    get_post_for_update,
    get_user_reaction,
    update_post_like_count,
    upsert_user_reaction,
)
from apps.engagement.schemas import (
    PostReactionData,
    PostReactionResponse,
    UpsertPostReactionRequest,
)
from common.enums import ReactionType
from common.responses import success_response

logger = logging.getLogger(__name__)


def format_user_reaction(reaction_type: ReactionType | None) -> str | None:
    if reaction_type is None:
        return None
    return reaction_type.value.upper()


def _counts_toward_like_count(reaction_type: ReactionType) -> bool:
    return True


def _like_count_delta(
    previous: ReactionType | None,
    current: ReactionType | None,
) -> int:
    if previous == current:
        return 0

    delta = 0
    if previous is not None and _counts_toward_like_count(previous):
        delta -= 1
    if current is not None and _counts_toward_like_count(current):
        delta += 1
    return delta


def _build_response(
    post_id: UUID,
    like_count: int,
    user_reaction: ReactionType | None,
    *,
    message: str,
) -> PostReactionResponse:
    return success_response(
        message,
        PostReactionData(
            post_id=post_id,
            like_count=like_count,
            user_reaction=format_user_reaction(user_reaction),
        ),
        response_cls=PostReactionResponse,
    )


async def upsert_post_reaction(
    db: AsyncSession,
    user_id: UUID,
    payload: UpsertPostReactionRequest,
) -> PostReactionResponse:
    post = await get_post_for_update(db, payload.post_id)
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")

    author_id = getattr(post, "author_user_id", None)
    if author_id is not None:
        from common.user_visibility import check_post_engagement_allowed
        await check_post_engagement_allowed(db, user_id, author_id)

    existing = await get_user_reaction(db, payload.post_id, user_id)
    previous_type = existing.reaction_type if existing else None
    new_type = payload.reaction_type
    removing = new_type is None

    if previous_type == new_type:
        message = (
            "Reaction removed successfully"
            if removing
            else "Reaction updated successfully"
        )
        return _build_response(
            payload.post_id,
            post.like_count,
            None if removing else new_type,
            message=message,
        )

    delta = _like_count_delta(previous_type, new_type)

    try:
        if removing:
            await delete_user_reaction(db, payload.post_id, user_id)
        else:
            await upsert_user_reaction(
                db,
                payload.post_id,
                user_id,
                new_type,
            )
        like_count = await update_post_like_count(db, payload.post_id, delta)
        await db.commit()
        if new_type == ReactionType.like and previous_type != ReactionType.like:
            from apps.recommendation.services.engagement_keyword_service import (
                apply_engagement_keyword_update_best_effort,
            )

            await apply_engagement_keyword_update_best_effort(
                db,
                user_id,
                payload.post_id,
                "like",
                added=True,
            )
        elif previous_type == ReactionType.like and new_type != ReactionType.like:
            from apps.recommendation.services.engagement_keyword_service import (
                apply_engagement_keyword_update_best_effort,
            )

            await apply_engagement_keyword_update_best_effort(
                db,
                user_id,
                payload.post_id,
                "like",
                added=False,
            )
    except IntegrityError:
        await db.rollback()
        logger.warning(
            "Duplicate reaction race for user_id=%s post_id=%s",
            user_id,
            payload.post_id,
        )
        existing = await get_user_reaction(db, payload.post_id, user_id)
        post = await get_post_for_update(db, payload.post_id)
        if post is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")
        current = existing.reaction_type if existing else None
        message = (
            "Reaction removed successfully"
            if removing
            else "Reaction updated successfully"
        )
        return _build_response(
            payload.post_id,
            post.like_count,
            None if removing else current,
            message=message,
        )
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to upsert reaction user_id=%s post_id=%s",
            user_id,
            payload.post_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update reaction",
        )

    message = (
        "Reaction removed successfully"
        if removing
        else "Reaction updated successfully"
    )
    return _build_response(
        payload.post_id,
        like_count,
        None if removing else new_type,
        message=message,
    )
