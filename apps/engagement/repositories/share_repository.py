from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.engagement.db_models import ShareEvent
from apps.feed.db_models import Post


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def get_user_share_event(
    db: AsyncSession,
    user_id: UUID,
    post_id: UUID,
) -> ShareEvent | None:
    stmt = select(ShareEvent).where(
        ShareEvent.user_id == user_id,
        ShareEvent.post_id == post_id,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def create_share_event(
    db: AsyncSession,
    user_id: UUID,
    post_id: UUID,
    *,
    now: datetime | None = None,
) -> ShareEvent:
    share_event = ShareEvent(
        user_id=user_id,
        post_id=post_id,
        created_at=now or utc_now(),
        updated_at=now or utc_now(),
    )
    db.add(share_event)
    return share_event


async def get_post_share_count(db: AsyncSession, post_id: UUID) -> int:
    stmt = select(Post.share_count).where(Post.id == post_id)
    result = (await db.execute(stmt)).scalar_one_or_none()
    return int(result or 0)


async def update_post_share_count(
    db: AsyncSession,
    post_id: UUID,
    delta: int,
) -> int:
    if delta == 0:
        post = await db.get(Post, post_id)
        return post.share_count if post else 0

    stmt = (
        update(Post)
        .where(Post.id == post_id)
        .values(share_count=func.greatest(Post.share_count + delta, 0))
        .returning(Post.share_count)
    )
    return int((await db.execute(stmt)).scalar_one())


async def count_share_events_for_post(db: AsyncSession, post_id: UUID) -> int:
    stmt = (
        select(func.count())
        .select_from(ShareEvent)
        .where(ShareEvent.post_id == post_id)
    )
    return int((await db.execute(stmt)).scalar_one())


async def fetch_share_counts(
    db: AsyncSession,
    post_ids: list[UUID],
) -> dict[UUID, int]:
    if not post_ids:
        return {}
    stmt = (
        select(ShareEvent.post_id, func.count())
        .where(ShareEvent.post_id.in_(post_ids))
        .group_by(ShareEvent.post_id)
    )
    rows = (await db.execute(stmt)).all()
    counts = {post_id: int(count) for post_id, count in rows}
    return {post_id: counts.get(post_id, 0) for post_id in post_ids}
