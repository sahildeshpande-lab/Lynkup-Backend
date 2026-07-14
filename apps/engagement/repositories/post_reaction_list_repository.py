from __future__ import annotations

from collections import defaultdict
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
    limit: int | None = 20,
) -> list[tuple[PostReaction, Profile | None, University | None]]:
    stmt = (
        select(PostReaction, Profile, University)
        .outerjoin(Profile, Profile.user_id == PostReaction.user_id)
        .outerjoin(University, University.id == Profile.university_id)
        .where(PostReaction.post_id == post_id)
        .order_by(PostReaction.created_at.desc())
        .offset(offset)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    if reaction_type is not None:
        stmt = stmt.where(PostReaction.reaction_type == reaction_type)

    return list((await db.execute(stmt)).all())


async def fetch_latest_reactors_for_posts(
    db: AsyncSession,
    post_ids: list[UUID],
    *,
    per_type_limit: int = 3,
) -> dict[UUID, list[tuple[PostReaction, Profile | None, University | None]]]:
    if not post_ids:
        return {}

    row_number = func.row_number().over(
        partition_by=(PostReaction.post_id, PostReaction.reaction_type),
        order_by=PostReaction.created_at.desc(),
    ).label("row_number")

    ranked = (
        select(
            PostReaction.id.label("reaction_id"),
            row_number,
        )
        .where(PostReaction.post_id.in_(post_ids))
        .subquery()
    )

    stmt = (
        select(PostReaction, Profile, University)
        .join(ranked, PostReaction.id == ranked.c.reaction_id)
        .outerjoin(Profile, Profile.user_id == PostReaction.user_id)
        .outerjoin(University, University.id == Profile.university_id)
        .where(ranked.c.row_number <= per_type_limit)
        .order_by(
            PostReaction.post_id,
            PostReaction.reaction_type,
            PostReaction.created_at.desc(),
        )
    )
    rows = (await db.execute(stmt)).all()

    by_post: dict[UUID, list[tuple[PostReaction, Profile | None, University | None]]] = defaultdict(list)
    for reaction, profile, university in rows:
        by_post[reaction.post_id].append((reaction, profile, university))
    return dict(by_post)
