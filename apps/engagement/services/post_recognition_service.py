from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.profiles.db_models import Profile
from common.post_recognition import select_post_recognition_milestone_for_notification

logger = logging.getLogger(__name__)

POST_RECOGNITION_DEDUP_SECONDS = 60


def _advisory_lock_keys(user_id: UUID, milestone: int) -> tuple[int, int]:
    return user_id.int % ((2**31) - 1), milestone


async def _author_first_name(db: AsyncSession, user_id: UUID) -> str | None:
    first_name = (
        await db.execute(select(Profile.first_name).where(Profile.user_id == user_id))
    ).scalar_one_or_none()
    if isinstance(first_name, str):
        stripped = first_name.strip()
        if stripped:
            return stripped
    return None


async def _try_acquire_post_recognition_lock(
    db: AsyncSession,
    *,
    user_id: UUID,
    milestone: int,
) -> bool:
    key1, key2 = _advisory_lock_keys(user_id, milestone)
    acquired = (
        await db.execute(
            text("SELECT pg_try_advisory_lock(:key1, :key2)"),
            {"key1": key1, "key2": key2},
        )
    ).scalar_one()
    return bool(acquired)


async def _release_post_recognition_lock(
    db: AsyncSession,
    *,
    user_id: UUID,
    milestone: int,
) -> None:
    key1, key2 = _advisory_lock_keys(user_id, milestone)
    await db.execute(
        text("SELECT pg_advisory_unlock(:key1, :key2)"),
        {"key1": key1, "key2": key2},
    )


async def notify_post_recognition_milestones_best_effort(
    db: AsyncSession,
    *,
    author_user_id: UUID,
    post_id: UUID,
    crossed_milestones: list[int],
) -> None:
    """Notify the post author about a like milestone, deduped across concurrent posts."""
    milestone = select_post_recognition_milestone_for_notification(crossed_milestones)
    if milestone is None:
        return

    lock_acquired = await _try_acquire_post_recognition_lock(
        db,
        user_id=author_user_id,
        milestone=milestone,
    )
    if not lock_acquired:
        logger.info(
            "Skipped post recognition notification author_user_id=%s milestone=%s reason=lock_busy",
            author_user_id,
            milestone,
        )
        return

    try:
        from apps.notifications.repositories.notification_repository import (
            has_recent_post_recognition_notification,
        )
        from apps.notifications.services.notification_service import notify_post_recognition

        if await has_recent_post_recognition_notification(
            db,
            recipient_user_id=author_user_id,
            milestone=milestone,
            within_seconds=POST_RECOGNITION_DEDUP_SECONDS,
        ):
            logger.info(
                "Skipped post recognition notification author_user_id=%s milestone=%s reason=recent_duplicate",
                author_user_id,
                milestone,
            )
            return

        first_name = await _author_first_name(db, author_user_id)
        await notify_post_recognition(
            db,
            author_user_id=author_user_id,
            post_id=post_id,
            milestone=milestone,
            first_name=first_name,
        )
    except Exception:
        logger.exception(
            "Failed post recognition notification author_user_id=%s post_id=%s milestone=%s",
            author_user_id,
            post_id,
            milestone,
        )
    finally:
        await _release_post_recognition_lock(
            db,
            user_id=author_user_id,
            milestone=milestone,
        )
