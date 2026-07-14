from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from apps.accounts.db_models import User
from apps.engagement.db_models import PostReaction
from apps.feed.db_models import Post, PostAttachment
from apps.profiles.db_models import Profile


async def count_user_liked_posts(db: AsyncSession, user_id: UUID) -> int:
    stmt = select(func.count(PostReaction.id)).where(PostReaction.user_id == user_id)
    return int((await db.execute(stmt)).scalar_one())


async def fetch_user_liked_posts(
    db: AsyncSession,
    user_id: UUID,
    *,
    offset: int = 0,
    limit: int | None = None,
) -> list[tuple]:
    author_profile = aliased(Profile, name="author_profile")
    moderator_user = aliased(User, name="moderator_user")
    moderator_profile = aliased(Profile, name="moderator_profile")

    stmt = (
        select(Post, author_profile, moderator_user, moderator_profile)
        .join(PostReaction, PostReaction.post_id == Post.id)
        .join(author_profile, author_profile.user_id == Post.author_user_id)
        .outerjoin(moderator_user, moderator_user.id == Post.moderator_id)
        .outerjoin(moderator_profile, moderator_profile.user_id == Post.moderator_id)
        .where(PostReaction.user_id == user_id)
        .options(selectinload(Post.attachments).selectinload(PostAttachment.media_asset))
        .order_by(PostReaction.updated_at.desc(), PostReaction.created_at.desc())
        .offset(offset)
    )
    if limit is not None:
        stmt = stmt.limit(limit)

    return list((await db.execute(stmt)).all())
