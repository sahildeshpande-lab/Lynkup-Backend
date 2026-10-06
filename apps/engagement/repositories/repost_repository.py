from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.engagement.db_models import Repost
from apps.feed.db_models import Post
from apps.profiles.db_models import Profile
from common.enums import FEED_VISIBLE_POST_STATES


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
        Repost.is_deleted.is_(False),
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


async def repair_orphaned_reposts_for_user(
    db: AsyncSession,
    user_id: UUID,
) -> None:
    """Mark repost rows deleted when their original post is deleted or rejected."""
    from common.enums import PostState

    removed_original_ids = select(Post.id).where(
        Post.state.in_((PostState.deleted, PostState.rejected))
    )
    await db.execute(
        update(Repost)
        .where(
            Repost.user_id == user_id,
            Repost.is_deleted.is_(False),
            Repost.post_id.in_(removed_original_ids),
        )
        .values(is_deleted=True)
    )


async def mark_reposts_deleted_for_removed_original(
    db: AsyncSession,
    post_id: UUID,
) -> None:
    """Soft-delete repost rows when the original post is deleted or rejected."""
    from common.enums import PostState

    post = await db.get(Post, post_id)
    if post is None or post.state not in (PostState.deleted, PostState.rejected):
        return

    await db.execute(
        update(Repost)
        .where(
            Repost.post_id == post_id,
            Repost.is_deleted.is_(False),
        )
        .values(is_deleted=True)
    )


async def list_active_reposter_user_ids(
    db: AsyncSession,
    post_id: UUID,
) -> list[UUID]:
    """User IDs with a live (non-deleted) repost of ``post_id``."""
    stmt = (
        select(Repost.user_id)
        .where(
            Repost.post_id == post_id,
            Repost.is_deleted.is_(False),
        )
        .distinct()
    )
    try:
        raw = (await db.execute(stmt)).scalars().all()
    except StopAsyncIteration:
        return []
    if not isinstance(raw, (list, tuple)):
        return []
    return [user_id for user_id in raw if user_id is not None]


async def count_active_reposts_for_user(
    db: AsyncSession,
    user_id: UUID,
) -> int:
    """Count live reposts whose original is still publicly visible."""
    stmt = (
        select(func.count())
        .select_from(Repost)
        .join(Post, Post.id == Repost.post_id)
        .where(
            Repost.user_id == user_id,
            Repost.is_deleted.is_(False),
            Post.state.in_(FEED_VISIBLE_POST_STATES),
        )
    )
    return int((await db.execute(stmt)).scalar_one() or 0)


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
