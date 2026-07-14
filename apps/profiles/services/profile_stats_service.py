from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from apps.profiles.db_models.profile_db_model import Profile
from apps.profiles.db_models.profile_stats_db_model import ProfileStats


async def get_or_create_profile_stats(
    db: AsyncSession,
    profile_id: UUID,
) -> ProfileStats:
    result = await db.execute(
        select(ProfileStats).where(ProfileStats.profile_id == profile_id)
    )
    stats = result.scalar_one_or_none()
    if stats:
        return stats

    stats = ProfileStats(profile_id=profile_id, connection_count=0)
    db.add(stats)
    await db.flush()
    return stats


async def get_connection_count_for_profile(
    db: AsyncSession,
    profile_id: UUID | None,
) -> int:
    if profile_id is None:
        return 0

    result = await db.execute(
        select(ProfileStats.connection_count).where(ProfileStats.profile_id == profile_id)
    )
    count = result.scalar_one_or_none()
    return int(count or 0)


async def increment_connection_counts_for_users(
    db: AsyncSession,
    user_id_1: UUID,
    user_id_2: UUID,
) -> None:
    for user_id in (user_id_1, user_id_2):
        profile_result = await db.execute(select(Profile).where(Profile.user_id == user_id))
        profile = profile_result.scalar_one_or_none()
        if not profile:
            continue
        stats = await get_or_create_profile_stats(db, profile.id)
        stats.connection_count += 1


async def decrement_connection_counts_for_users(
    db: AsyncSession,
    user_id_1: UUID,
    user_id_2: UUID,
) -> None:
    """Decrease connection counts for both users without going below zero."""
    for user_id in (user_id_1, user_id_2):
        profile_result = await db.execute(select(Profile).where(Profile.user_id == user_id))
        profile = profile_result.scalar_one_or_none()
        if not profile:
            continue
        stats = await get_or_create_profile_stats(db, profile.id)
        stats.connection_count = max((stats.connection_count or 0) - 1, 0)


async def increment_posts_count_for_user(
    db: AsyncSession,
    user_id: UUID,
) -> None:
    """Increase the published posts count for a user's profile."""
    profile_result = await db.execute(select(Profile).where(Profile.user_id == user_id))
    profile = profile_result.scalar_one_or_none()
    if not profile:
        return
    profile.posts_count = (profile.posts_count or 0) + 1


async def decrement_posts_count_for_user(
    db: AsyncSession,
    user_id: UUID,
) -> None:
    """Decrease the published posts count for a user's profile without going below zero."""
    profile_result = await db.execute(select(Profile).where(Profile.user_id == user_id))
    profile = profile_result.scalar_one_or_none()
    if not profile:
        return
    profile.posts_count = max((profile.posts_count or 0) - 1, 0)
