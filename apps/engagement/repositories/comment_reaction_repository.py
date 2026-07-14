from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.engagement.db_models import Comment, CommentReaction
from common.enums import ReactionType


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def get_user_comment_reaction(
    db: AsyncSession,
    comment_id: UUID,
    user_id: UUID,
) -> CommentReaction | None:
    stmt = select(CommentReaction).where(
        CommentReaction.comment_id == comment_id,
        CommentReaction.user_id == user_id,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def fetch_user_comment_reactions(
    db: AsyncSession,
    user_id: UUID,
    comment_ids: list[UUID],
) -> dict[UUID, ReactionType]:
    if not comment_ids:
        return {}
    stmt = select(CommentReaction).where(
        CommentReaction.user_id == user_id,
        CommentReaction.comment_id.in_(comment_ids),
    )
    rows = (await db.execute(stmt)).scalars().all()
    return {reaction.comment_id: reaction.reaction_type for reaction in rows}


async def upsert_user_comment_reaction(
    db: AsyncSession,
    comment_id: UUID,
    user_id: UUID,
    reaction_type: ReactionType,
    *,
    now: datetime | None = None,
) -> CommentReaction:
    timestamp = now or utc_now()
    existing = await get_user_comment_reaction(db, comment_id, user_id)
    if existing is None:
        reaction = CommentReaction(
            comment_id=comment_id,
            user_id=user_id,
            reaction_type=reaction_type,
            created_at=timestamp,
            updated_at=timestamp,
        )
        db.add(reaction)
        return reaction

    existing.reaction_type = reaction_type
    existing.updated_at = timestamp
    db.add(existing)
    return existing


async def delete_user_comment_reaction(
    db: AsyncSession,
    comment_id: UUID,
    user_id: UUID,
) -> CommentReaction | None:
    existing = await get_user_comment_reaction(db, comment_id, user_id)
    if existing is None:
        return None
    await db.delete(existing)
    return existing


async def update_comment_like_count(
    db: AsyncSession,
    comment_id: UUID,
    delta: int,
) -> int:
    if delta == 0:
        comment = await db.get(Comment, comment_id)
        return comment.like_count if comment else 0

    stmt = (
        update(Comment)
        .where(Comment.id == comment_id)
        .values(like_count=func.greatest(Comment.like_count + delta, 0))
        .returning(Comment.like_count)
    )
    return int((await db.execute(stmt)).scalar_one())
