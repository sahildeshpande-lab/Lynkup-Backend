from __future__ import annotations
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from common.enums import PostState
from apps.feed.db_models import Post
from apps.connections.services.recommendation_service import get_user_connections

async def get_feed_service(
    current_user_id: UUID,
    db: AsyncSession
) -> list[Post]:
    """
    Get public feed posts. Connection posts first (by created_at DESC),
    then non-connection posts (by created_at DESC).
    """
    # Fetch user connections
    connection_ids = await get_user_connections(db, current_user_id)

    # Fetch all published posts ordered by created_at DESC
    stmt = select(Post).where(Post.state == PostState.published).order_by(Post.created_at.desc())
    result = await db.execute(stmt)
    posts = list(result.scalars().all())

    # Split posts into connection posts and non-connection posts
    connection_posts = [p for p in posts if p.author_user_id in connection_ids]
    non_connection_posts = [p for p in posts if p.author_user_id not in connection_ids]

    # Combine them (both lists are already ordered by created_at DESC)
    return connection_posts + non_connection_posts
