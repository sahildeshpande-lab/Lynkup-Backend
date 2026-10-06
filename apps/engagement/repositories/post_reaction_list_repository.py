from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.engagement.db_models import PostReaction
from apps.feed.db_models import Post
from apps.profiles.db_models import Profile
from apps.profiles.db_models.university_db_model import University
from common.enums import PostState, ReactionType

# Projected columns only — same ROW_NUMBER semantics as prior ORM query, no join-back.
_LATEST_REACTORS_FOR_POSTS_SQL = """
WITH ranked_reactions AS (
    SELECT
        pr.post_id,
        pr.user_id,
        pr.reaction_type,
        pr.created_at,
        p.user_id AS profile_user_id,
        p.first_name,
        p.last_name,
        p.profile_photo_url,
        p.bio,
        row_number() OVER (
            PARTITION BY pr.post_id, pr.reaction_type
            ORDER BY pr.created_at DESC
        ) AS rn
    FROM post_reactions pr
    LEFT JOIN profiles p ON p.user_id = pr.user_id
    WHERE pr.post_id IN :post_ids
)
SELECT
    post_id,
    user_id,
    reaction_type,
    created_at,
    profile_user_id,
    first_name,
    last_name,
    profile_photo_url,
    bio
FROM ranked_reactions
WHERE rn <= :per_type_limit
ORDER BY post_id, reaction_type, created_at DESC
"""


@dataclass(frozen=True)
class LatestReactorReactionRow:
    """Lightweight reaction fields for latest-reactor formatting."""

    post_id: UUID
    user_id: UUID
    reaction_type: ReactionType
    created_at: datetime


@dataclass(frozen=True)
class LatestReactorProfileRow:
    """Lightweight profile fields for latest-reactor formatting."""

    user_id: UUID
    first_name: str | None
    last_name: str | None
    profile_photo_url: str | None
    bio: str | None
    # Not emitted in PostReactorProfile; present so format_engagement_author() stays unchanged.
    major: str | None = None
    minor: str | None = None
    edu_level: str | None = None


LatestReactorRow = tuple[LatestReactorReactionRow, LatestReactorProfileRow | None, None]


def _coerce_reaction_type(value: Any) -> ReactionType:
    if isinstance(value, ReactionType):
        return value
    return ReactionType(str(value).lower())


def _map_latest_reactor_rows(
    rows: list[Any],
) -> dict[UUID, list[LatestReactorRow]]:
    by_post: dict[UUID, list[LatestReactorRow]] = defaultdict(list)
    for row in rows:
        profile: LatestReactorProfileRow | None = None
        profile_user_id = row.get("profile_user_id")
        if profile_user_id is not None:
            profile = LatestReactorProfileRow(
                user_id=profile_user_id,
                first_name=row.get("first_name"),
                last_name=row.get("last_name"),
                profile_photo_url=row.get("profile_photo_url"),
                bio=row.get("bio"),
            )
        reaction = LatestReactorReactionRow(
            post_id=row["post_id"],
            user_id=row["user_id"],
            reaction_type=_coerce_reaction_type(row["reaction_type"]),
            created_at=row["created_at"],
        )
        by_post[reaction.post_id].append((reaction, profile, None))
    return dict(by_post)


async def post_exists(db: AsyncSession, post_id: UUID) -> bool:
    stmt = select(Post.id).where(
        Post.id == post_id,
        Post.state.notin_([PostState.deleted, PostState.rejected]),
    )
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
) -> dict[UUID, list[LatestReactorRow]]:
    if not post_ids:
        return {}

    unique_ids = list({post_id for post_id in post_ids if post_id is not None})
    stmt = text(_LATEST_REACTORS_FOR_POSTS_SQL).bindparams(
        bindparam("post_ids", expanding=True),
        bindparam("per_type_limit"),
    )
    result = await db.execute(
        stmt,
        {"post_ids": unique_ids, "per_type_limit": per_type_limit},
    )
    return _map_latest_reactor_rows(result.mappings().all())
