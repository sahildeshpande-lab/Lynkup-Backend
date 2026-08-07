from __future__ import annotations

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
