from __future__ import annotations

from typing import Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from apps.feed.db_models import Post
from common.enums import PostState

_PUBLISH_REVIEW_STATES = (PostState.published, PostState.hidden)


def _build_reviewed_posts_filter(
    moderator_id: UUID | None,
    status: Literal["publish", "flag"] | None,
):
    filters = [
        Post.is_moderator_reviewed.is_(True),
    ]
    if moderator_id is not None:
        filters.append(Post.moderator_id == moderator_id)
    if status == "publish":
        filters.append(Post.state.in_(_PUBLISH_REVIEW_STATES))
    elif status == "flag":
        filters.append(Post.state == PostState.flagged)
    return filters


async def count_reviewed_posts_for_moderator(
    db: AsyncSession,
    moderator_id: UUID | None,
    status: Literal["publish", "flag"] | None = None,
) -> int:
    filters = _build_reviewed_posts_filter(moderator_id, status)
    stmt = select(func.count(Post.id)).where(*filters)
    return int((await db.execute(stmt)).scalar_one())


async def fetch_reviewed_posts_for_moderator(
    db: AsyncSession,
    moderator_id: UUID | None,
    *,
    status: Literal["publish", "flag"] | None = None,
    offset: int = 0,
    limit: int | None = None,
) -> list[tuple[Post, object | None]]:
    from apps.profiles.db_models import Profile

    filters = _build_reviewed_posts_filter(moderator_id, status)
    stmt = (
        select(Post, Profile)
        .outerjoin(Profile, Profile.user_id == Post.author_user_id)
        .where(*filters)
        .order_by(Post.reviewed_at.desc(), Post.updated_at.desc())
        .offset(offset)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    return list(result.all())


async def user_exists(db: AsyncSession, user_id: UUID) -> bool:
    """Return whether a user exists."""
    result = await db.execute(select(User.id).where(User.id == user_id))
    return result.scalar_one_or_none() is not None


async def fetch_posts_by_state(
    db: AsyncSession,
    *,
    state: PostState,
    user_id: UUID | None = None,
) -> list[Post]:
    """Fetch posts filtered by state and optional author, newest first."""
    filters = [Post.state == state]
    if user_id is not None:
        filters.append(Post.author_user_id == user_id)

    result = await db.execute(
        select(Post)
        .where(*filters)
        .order_by(Post.created_at.desc())
    )
    return list(result.scalars().all())
