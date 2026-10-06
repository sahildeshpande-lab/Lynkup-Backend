from __future__ import annotations

from uuid import UUID

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select as sqlmodel_select

from apps.accounts.db_models import User
from apps.profiles.db_models.profile_db_model import Profile
from common.enums import UserStatus


async def get_connection_count_for_profile(
    db: AsyncSession,
    user_id: UUID | None,
) -> int:
    """Count distinct active connections for a user, excluding hidden peers.

    Matches:

        SELECT COUNT(DISTINCT CASE
            WHEN c.user_high_id = :user_id THEN c.user_low_id
            ELSE c.user_high_id
        END)
        FROM connections c
        JOIN users u ON u.id = CASE
            WHEN c.user_high_id = :user_id THEN c.user_low_id
            ELSE c.user_high_id
        END
        WHERE (c.user_high_id = :user_id OR c.user_low_id = :user_id)
          AND u.status NOT IN ('suspended', 'banned')
          AND u.is_deleted = false
    """
    if user_id is None:
        return 0

    from apps.connections.db_models import Connection

    peer_id = case(
        (Connection.user_high_id == user_id, Connection.user_low_id),
        else_=Connection.user_high_id,
    )
    stmt = (
        select(func.count(func.distinct(peer_id)))
        .select_from(Connection)
        .join(User, User.id == peer_id)
        .where(
            or_(Connection.user_high_id == user_id, Connection.user_low_id == user_id),
            Connection.is_active.is_(True),
            User.status.notin_((UserStatus.suspended, UserStatus.banned)),
            User.is_deleted.is_(False),
        )
    )
    count = (await db.execute(stmt)).scalar_one()
    return int(count or 0)


async def count_public_posts_for_user(db: AsyncSession, user_id: UUID) -> int:
    """Count authored + reposted posts visible on public profile surfaces."""
    from apps.engagement.repositories.repost_repository import count_active_reposts_for_user
    from apps.feed.repositories.post_repository import count_posts_by_state
    from common.enums import FEED_VISIBLE_POST_STATES

    authored = await count_posts_by_state(
        db,
        state=FEED_VISIBLE_POST_STATES,
        user_id=user_id,
    )
    reposts = await count_active_reposts_for_user(db, user_id)
    return int(authored or 0) + int(reposts or 0)


async def recalculate_posts_count_for_user(
    db: AsyncSession,
    user_id: UUID,
) -> None:
    """Set cached posts_count from authored + qualifying repost rows."""
    from apps.engagement.repositories.repost_repository import (
        repair_orphaned_reposts_for_user,
    )

    profile_result = await db.execute(sqlmodel_select(Profile).where(Profile.user_id == user_id))
    profile = profile_result.scalar_one_or_none()
    if not profile:
        return
    await repair_orphaned_reposts_for_user(db, user_id)
    profile.posts_count = await count_public_posts_for_user(db, user_id)


async def increment_posts_count_for_user(
    db: AsyncSession,
    user_id: UUID,
) -> None:
    """Increase the published posts count for a user's profile."""
    await recalculate_posts_count_for_user(db, user_id)


async def decrement_posts_count_for_user(
    db: AsyncSession,
    user_id: UUID,
) -> None:
    """Decrease the published posts count for a user's profile without going below zero."""
    await recalculate_posts_count_for_user(db, user_id)


async def recalculate_reposter_posts_counts(
    db: AsyncSession,
    post_id: UUID,
    *,
    exclude_user_id: UUID | None = None,
) -> None:
    """Refresh cached posts_count for every live reposter of ``post_id``."""
    from apps.engagement.repositories.repost_repository import list_active_reposter_user_ids

    for user_id in await list_active_reposter_user_ids(db, post_id):
        if exclude_user_id is not None and user_id == exclude_user_id:
            continue
        await recalculate_posts_count_for_user(db, user_id)


async def adjust_reposter_posts_counts(
    db: AsyncSession,
    post_id: UUID,
    *,
    decrement: bool,
    exclude_user_id: UUID | None = None,
) -> None:
    """Refresh each live reposter's cached posts_count after original visibility changes.

    Flag/escalate hide the original from profile lists, so those reposts must
    drop out of the cache. Publish/reinstate puts them back. The Repost rows
    stay active so a later unflag can restore the cards without re-reposting.
    """
    del decrement
    await recalculate_reposter_posts_counts(
        db,
        post_id,
        exclude_user_id=exclude_user_id,
    )


async def sync_posts_count_for_visibility_change(
    db: AsyncSession,
    *,
    post_id: UUID,
    author_user_id: UUID,
    was_counted: bool,
    now_counted: bool,
) -> None:
    """Update author + live-reposter caches when a post enters or leaves public count."""
    if was_counted == now_counted:
        return
    await recalculate_posts_count_for_user(db, author_user_id)
    await recalculate_reposter_posts_counts(
        db,
        post_id,
        exclude_user_id=author_user_id,
    )


async def adjust_counts_for_deleting_user(
    db: AsyncSession,
    user_id: UUID,
) -> None:
    """Legacy helper: zero posts count and deactivate LynkUps for a user.

    Account-deletion grace period no longer calls this — content and social
    relationships must remain intact until permanent purge. Kept for any
    explicit callers that still need the old behavior.
    """
    from apps.connections.db_models import Connection

    profile_result = await db.execute(sqlmodel_select(Profile).where(Profile.user_id == user_id))
    profile = profile_result.scalar_one_or_none()
    if profile is not None:
        profile.posts_count = 0

    connections = (
        await db.execute(
            sqlmodel_select(Connection).where(
                Connection.is_active == True,  # noqa: E712
                or_(
                    Connection.user_low_id == user_id,
                    Connection.user_high_id == user_id,
                ),
            )
        )
    ).scalars().all()

    for connection in connections:
        connection.is_active = False
        db.add(connection)
