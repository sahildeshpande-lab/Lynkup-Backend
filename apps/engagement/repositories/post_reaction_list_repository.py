from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.engagement.db_models import PostReaction
from apps.feed.db_models import Post
from apps.profiles.db_models import Profile
from apps.profiles.db_models.university_db_model import University
from common.enums import ReactionType


async def post_exists(db: AsyncSession, post_id: UUID) -> bool:
    stmt = select(Post.id).where(Post.id == post_id)
    return (await db.execute(stmt)).scalar_one_or_none() is not None


async def count_post_reactions(
    db: AsyncSession,
    post_id: UUID,
    reaction_type: ReactionType | None = None,
) -> int:
    stmt = select(func.count()).select_from(PostReaction).where(PostReaction.post_id == post_id)
    if reaction_type is not None:
        stmt = stmt.where(PostReaction.reaction_type == reaction_type)
    return int((await db.execute(stmt)).scalar_one())


async def fetch_reaction_summary_counts(
    db: AsyncSession,
    post_id: UUID,
) -> dict[ReactionType, int]:
    stmt = (
        select(PostReaction.reaction_type, func.count())
        .where(PostReaction.post_id == post_id)
        .group_by(PostReaction.reaction_type)
    )
    rows = (await db.execute(stmt)).all()
    return {reaction_type: int(count) for reaction_type, count in rows}


async def fetch_post_reactors(
    db: AsyncSession,
    post_id: UUID,
    *,
    reaction_type: ReactionType | None = None,
    offset: int = 0,
    limit: int = 20,
) -> list[tuple[PostReaction, Profile | None, University | None]]:
    stmt = (
        select(PostReaction, Profile, University)
        .outerjoin(Profile, Profile.user_id == PostReaction.user_id)
        .outerjoin(University, University.id == Profile.university_id)
        .where(PostReaction.post_id == post_id)
        .order_by(PostReaction.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    if reaction_type is not None:
        stmt = stmt.where(PostReaction.reaction_type == reaction_type)

    return list((await db.execute(stmt)).all())
