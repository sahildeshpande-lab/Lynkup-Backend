from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.engagement.db_models import Bookmark
from apps.feed.db_models import Post


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def get_user_bookmark(
    db: AsyncSession,
    user_id: UUID,
    post_id: UUID,
) -> Bookmark | None:
    stmt = select(Bookmark).where(
        Bookmark.user_id == user_id,
        Bookmark.post_id == post_id,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def create_bookmark(
    db: AsyncSession,
    user_id: UUID,
    post_id: UUID,
    *,
    now: datetime | None = None,
) -> Bookmark:
    timestamp = now or utc_now()
    bookmark = Bookmark(
        user_id=user_id,
        post_id=post_id,
        created_at=timestamp,
        updated_at=timestamp,
    )
    db.add(bookmark)
    return bookmark


async def delete_bookmark(
    db: AsyncSession,
    bookmark: Bookmark,
) -> None:
    await db.delete(bookmark)


async def count_user_bookmarks(db: AsyncSession, user_id: UUID) -> int:
    from apps.accounts.db_models import User
    from common.user_visibility import visible_user_filters
    from sqlalchemy.orm import aliased

    author_user = aliased(User, name="author_user")
    stmt = (
        select(func.count(Bookmark.id))
        .join(Post, Post.id == Bookmark.post_id)
        .join(author_user, author_user.id == Post.author_user_id)
        .where(Bookmark.user_id == user_id, *visible_user_filters(author_user))
    )
    return int((await db.execute(stmt)).scalar_one())


async def fetch_user_bookmarked_posts(
    db: AsyncSession,
    user_id: UUID,
    *,
    offset: int = 0,
    limit: int | None = None,
) -> list[tuple]:
    from apps.accounts.db_models import User
    from apps.feed.db_models import PostAttachment
    from apps.profiles.db_models import Profile
    from common.user_visibility import visible_user_filters
    from sqlalchemy.orm import aliased, selectinload

    author_profile = aliased(Profile, name="author_profile")
    author_user = aliased(User, name="author_user")
    moderator_user = aliased(User, name="moderator_user")
    moderator_profile = aliased(Profile, name="moderator_profile")

    stmt = (
        select(Post, author_profile, moderator_user, moderator_profile)
        .join(Bookmark, Bookmark.post_id == Post.id)
        .join(author_user, author_user.id == Post.author_user_id)
        .join(author_profile, author_profile.user_id == Post.author_user_id)
        .outerjoin(moderator_user, moderator_user.id == Post.moderator_id)
        .outerjoin(moderator_profile, moderator_profile.user_id == Post.moderator_id)
        .where(Bookmark.user_id == user_id, *visible_user_filters(author_user))
        .options(selectinload(Post.attachments).selectinload(PostAttachment.media_asset))
        .order_by(Bookmark.updated_at.desc(), Bookmark.created_at.desc())
        .offset(offset)
    )
    if limit is not None:
        stmt = stmt.limit(limit)

    return list((await db.execute(stmt)).all())
