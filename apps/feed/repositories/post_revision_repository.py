from __future__ import annotations

from collections.abc import Collection
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.feed.db_models import PostRevision


async def get_processing_events(
    db: AsyncSession,
    post_id: UUID,
) -> list[PostRevision]:
    """
    Return revisions that reopened moderation for ``post_id``, newest first.

    These power synthetic ``processing`` rows on the status-history timeline.
    """
    stmt = (
        select(PostRevision)
        .where(
            PostRevision.post_id == post_id,
            PostRevision.triggered_moderation_review.is_(True),
        )
        .order_by(PostRevision.created_at.desc(), PostRevision.id.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def posts_with_triggered_moderation_review(
    db: AsyncSession,
    post_ids: Collection[UUID],
) -> set[UUID]:
    """Return post IDs that have at least one revision which reopened moderation."""
    ids = list(post_ids)
    if not ids:
        return set()

    stmt = (
        select(PostRevision.post_id)
        .where(
            PostRevision.post_id.in_(ids),
            PostRevision.triggered_moderation_review.is_(True),
        )
        .distinct()
    )
    result = await db.execute(stmt)
    return set(result.scalars().all())
