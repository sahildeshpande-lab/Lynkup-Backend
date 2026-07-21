from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.engagement.db_models import Comment
from apps.feed.db_models import Post
from apps.accounts.db_models import User
from apps.profiles.db_models import Profile
from apps.profiles.db_models.university_db_model import University
from common.user_visibility import visible_user_filters


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def post_exists(db: AsyncSession, post_id: UUID) -> bool:
    stmt = (
        select(Post.id)
        .join(User, User.id == Post.author_user_id)
        .where(Post.id == post_id, *visible_user_filters(User))
    )
    return (await db.execute(stmt)).scalar_one_or_none() is not None


async def get_comment_by_id(db: AsyncSession, comment_id: UUID) -> Comment | None:
    stmt = select(Comment).where(Comment.id == comment_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_comment_for_update(db: AsyncSession, comment_id: UUID) -> Comment | None:
    stmt = select(Comment).where(Comment.id == comment_id).with_for_update()
    return (await db.execute(stmt)).scalar_one_or_none()


async def create_comment(
    db: AsyncSession,
    *,
    post_id: UUID,
    user_id: UUID,
    comment_text: str,
    parent_comment_id: UUID | None,
    level: int,
    now: datetime | None = None,
) -> Comment:
    timestamp = now or utc_now()
    comment = Comment(
        post_id=post_id,
        user_id=user_id,
        parent_comment_id=parent_comment_id,
        level=level,
        comment_text=comment_text,
        created_at=timestamp,
        updated_at=timestamp,
    )
    db.add(comment)
    return comment


async def count_top_level_comments(db: AsyncSession, post_id: UUID) -> int:
    stmt = (
        select(func.count())
        .select_from(Comment)
        .join(User, User.id == Comment.user_id)
        .where(
            Comment.post_id == post_id,
            Comment.parent_comment_id.is_(None),
            *visible_user_filters(User),
        )
    )
    return int((await db.execute(stmt)).scalar_one())


async def update_post_comment_count(
    db: AsyncSession,
    post_id: UUID,
    delta: int,
) -> int:
    if delta == 0:
        post = await db.get(Post, post_id)
        return post.comment_count if post else 0

    stmt = (
        update(Post)
        .where(Post.id == post_id)
        .values(comment_count=func.greatest(Post.comment_count + delta, 0))
        .returning(Post.comment_count)
    )
    return int((await db.execute(stmt)).scalar_one())


async def fetch_top_level_comments(
    db: AsyncSession,
    post_id: UUID,
    *,
    offset: int = 0,
    limit: int | None = 20,
) -> list[tuple[Comment, Profile | None, University | None]]:
    stmt = (
        select(Comment, Profile, University)
        .join(User, User.id == Comment.user_id)
        .outerjoin(Profile, Profile.user_id == Comment.user_id)
        .outerjoin(University, University.id == Profile.university_id)
        .where(
            Comment.post_id == post_id,
            Comment.parent_comment_id.is_(None),
            *visible_user_filters(User),
        )
        .order_by(Comment.created_at.desc())
        .offset(offset)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    return list((await db.execute(stmt)).all())


async def fetch_comments_by_parent_ids(
    db: AsyncSession,
    parent_ids: list[UUID],
) -> list[Comment]:
    if not parent_ids:
        return []
    stmt = (
        select(Comment)
        .join(User, User.id == Comment.user_id)
        .where(
            Comment.parent_comment_id.in_(parent_ids),
            *visible_user_filters(User),
        )
        .order_by(Comment.created_at.asc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def fetch_profiles_by_user_ids(
    db: AsyncSession,
    user_ids: list[UUID],
) -> dict[UUID, tuple[Profile | None, University | None]]:
    if not user_ids:
        return {}
    stmt = (
        select(Profile, University)
        .outerjoin(University, University.id == Profile.university_id)
        .where(Profile.user_id.in_(user_ids))
    )
    rows = (await db.execute(stmt)).all()
    return {profile.user_id: (profile, university) for profile, university in rows}


async def increment_reply_count(db: AsyncSession, comment_id: UUID) -> int:
    stmt = (
        update(Comment)
        .where(Comment.id == comment_id)
        .values(reply_count=Comment.reply_count + 1)
        .returning(Comment.reply_count)
    )
    return int((await db.execute(stmt)).scalar_one())


async def mark_comment_deleted(db: AsyncSession, comment: Comment, *, now: datetime | None = None) -> Comment:
    timestamp = now or utc_now()
    comment.is_deleted = True
    comment.updated_at = timestamp
    db.add(comment)
    return comment
