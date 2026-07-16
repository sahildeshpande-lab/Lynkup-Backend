from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.engagement.db_models import Repost
from apps.feed.db_models import Post
from apps.profiles.db_models import Profile


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def get_profile_id_for_user(db: AsyncSession, user_id: UUID) -> UUID | None:
    stmt = select(Profile.id).where(Profile.user_id == user_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_user_repost(
    db: AsyncSession,
    profile_id: UUID,
    post_id: UUID,
) -> Repost | None:
    stmt = select(Repost).where(
        Repost.profile_id == profile_id,
        Repost.post_id == post_id,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def create_repost(
    db: AsyncSession,
    profile_id: UUID,
    user_id: UUID,
    post_id: UUID,
    *,
    now: datetime | None = None,
) -> Repost:
    repost = Repost(
        profile_id=profile_id,
        user_id=user_id,
        post_id=post_id,
        created_at=now or utc_now(),
    )
    db.add(repost)
    return repost


async def delete_repost(
    db: AsyncSession,
    repost: Repost,
) -> None:
    await db.delete(repost)


async def update_post_repost_count(
    db: AsyncSession,
    post_id: UUID,
    delta: int,
) -> int:
    if delta == 0:
        post = await db.get(Post, post_id)
        return post.repost_count if post else 0

    stmt = (
        update(Post)
        .where(Post.id == post_id)
        .values(repost_count=func.greatest(Post.repost_count + delta, 0))
        .returning(Post.repost_count)
    )
    return int((await db.execute(stmt)).scalar_one())
