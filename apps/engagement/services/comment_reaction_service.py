from __future__ import annotations

import logging
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.engagement.repositories.comment_reaction_repository import (
    delete_user_comment_reaction,
    get_user_comment_reaction,
    update_comment_like_count,
    upsert_user_comment_reaction,
)
from apps.engagement.repositories.comment_repository import get_comment_by_id
from apps.engagement.schemas import (
    CommentReactionData,
    CommentReactionResponse,
    UpsertCommentReactionRequest,
)
from apps.engagement.services.reaction_service import format_user_reaction
from common.enums import ReactionType
from common.responses import success_response

logger = logging.getLogger(__name__)


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
    comment_id: UUID,
    like_count: int,
    user_reaction: ReactionType | None,
    *,
    message: str,
) -> CommentReactionResponse:
    return success_response(
        message,
        CommentReactionData(
            comment_id=comment_id,
            like_count=like_count,
            user_reaction=format_user_reaction(user_reaction),
        ),
        response_cls=CommentReactionResponse,
    )


async def upsert_comment_reaction(
    db: AsyncSession,
    user_id: UUID,
    payload: UpsertCommentReactionRequest,
) -> CommentReactionResponse:
    comment = await get_comment_by_id(db, payload.comment_id)
    if comment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found")
    if comment.is_deleted:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot react to a deleted comment")

    existing = await get_user_comment_reaction(db, payload.comment_id, user_id)
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
            payload.comment_id,
            comment.like_count,
            None if removing else new_type,
            message=message,
        )

    delta = _like_count_delta(previous_type, new_type)

    try:
        if removing:
            await delete_user_comment_reaction(db, payload.comment_id, user_id)
        else:
            await upsert_user_comment_reaction(
                db,
                payload.comment_id,
                user_id,
                new_type,
            )
        like_count = await update_comment_like_count(db, payload.comment_id, delta)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        logger.warning(
            "Duplicate comment reaction race for user_id=%s comment_id=%s",
            user_id,
            payload.comment_id,
        )
        existing = await get_user_comment_reaction(db, payload.comment_id, user_id)
        comment = await get_comment_by_id(db, payload.comment_id)
        if comment is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found")
        current = existing.reaction_type if existing else None
        message = (
            "Reaction removed successfully"
            if removing
            else "Reaction updated successfully"
        )
        return _build_response(
            payload.comment_id,
            comment.like_count,
            None if removing else current,
            message=message,
        )
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to upsert comment reaction user_id=%s comment_id=%s",
            user_id,
            payload.comment_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update comment reaction",
        )

    message = (
        "Reaction removed successfully"
        if removing
        else "Reaction updated successfully"
    )
    return _build_response(
        payload.comment_id,
        like_count,
        None if removing else new_type,
        message=message,
    )
