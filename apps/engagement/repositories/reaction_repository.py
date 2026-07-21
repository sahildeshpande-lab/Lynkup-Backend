from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.engagement.db_models import PostReaction
from apps.feed.db_models import Post


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def get_post_for_update(db: AsyncSession, post_id: UUID) -> Post | None:
    stmt = select(Post).where(Post.id == post_id).with_for_update()
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_user_reaction(
    db: AsyncSession,
    post_id: UUID,
    user_id: UUID,
) -> PostReaction | None:
    stmt = select(PostReaction).where(
        PostReaction.post_id == post_id,
        PostReaction.user_id == user_id,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def upsert_user_reaction(
    db: AsyncSession,
    post_id: UUID,
    user_id: UUID,
    reaction_type,
    *,
    now: datetime | None = None,
) -> PostReaction:
    timestamp = now or utc_now()
    existing = await get_user_reaction(db, post_id, user_id)
    if existing is None:
        reaction = PostReaction(
            post_id=post_id,
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


async def delete_user_reaction(
    db: AsyncSession,
    post_id: UUID,
    user_id: UUID,
) -> PostReaction | None:
    existing = await get_user_reaction(db, post_id, user_id)
    if existing is None:
        return None
    await db.delete(existing)
    return existing


async def update_post_like_count(
    db: AsyncSession,
    post_id: UUID,
    delta: int,
) -> int:
    if delta == 0:
        post = await db.get(Post, post_id)
        return post.like_count if post else 0

    stmt = (
        update(Post)
        .where(Post.id == post_id)
        .values(like_count=func.greatest(Post.like_count + delta, 0))
        .returning(Post.like_count)
    )
    return int((await db.execute(stmt)).scalar_one())
