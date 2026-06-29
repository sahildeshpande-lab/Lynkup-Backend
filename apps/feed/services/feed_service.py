from __future__ import annotations
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from common.enums import PostState
from apps.feed.db_models import Post

async def get_feed_service(
    current_user_id: UUID,
    db: AsyncSession
) -> list[Post]:
    """
    Get public feed posts ordered by latest first.
    """
    # Shows published posts from all users
    stmt = select(Post).where(Post.state == PostState.published).order_by(Post.created_at.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())
