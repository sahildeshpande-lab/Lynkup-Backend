from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from apps.connections.services.recommendation_service import get_user_connections
from apps.feed.db_models import Post
from apps.feed.repositories.feed_repository import (
    count_feed_posts,
    fetch_feed_posts,
    fetch_viewer_profile,
)


async def get_feed_service(
    current_user_id: UUID,
    db: AsyncSession,
    page: int | None = None,
    page_size: int | None = None,
    include_total: bool = False,
) -> list[Post] | tuple[list[Post], int]:
    """
    Return feed posts with profile-visibility filters applied in SQL.
    When the viewer has major/minor/university, matching posts appear first,
    then all other eligible posts; each group is ordered by created_at DESC.
    """
    viewer_profile = await fetch_viewer_profile(db, current_user_id)
    connection_ids = await get_user_connections(db, current_user_id)
    total_items = await count_feed_posts(db, current_user_id, viewer_profile, connection_ids)

    if page is None and page_size is None:
        offset = 0
        limit = None
    else:
        p = page or 1
        ps = page_size or 20
        offset = (p - 1) * ps
        limit = ps

    rows = await fetch_feed_posts(
        db,
        current_user_id,
        viewer_profile,
        connection_ids,
        offset=offset,
        limit=limit,
    )

    posts = []
    for post, profile in rows:
        post._author_profile = profile
        posts.append(post)

    if include_total:
        return posts, total_items
    return posts
