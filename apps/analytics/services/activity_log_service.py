from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from apps.analytics.repositories import create_user_activity_log
from common.enums import UserActivityLogType

logger = logging.getLogger(__name__)


async def add_user_activity_log(
    db: AsyncSession,
    user_id: UUID,
    activity_log: UserActivityLogType,
    *,
    commit: bool = False,
) -> None:
    """
    Persist a meaningful user activity.

    When ``commit`` is False (default), the row is flushed into the current
    transaction and the caller remains responsible for commit/rollback — matching
    ``log_security_event``.

    When ``commit`` is True, the activity is committed immediately (for call sites
    that already committed the primary business operation).
    """
    await create_user_activity_log(db, user_id, activity_log)
    if commit:
        await db.commit()


async def add_user_activity_log_best_effort(
    db: AsyncSession,
    user_id: UUID,
    activity_log: UserActivityLogType,
) -> None:
    """Record activity after a successful business commit; never fail the caller."""
    try:
        await add_user_activity_log(db, user_id, activity_log, commit=True)
    except Exception:
        logger.exception(
            "Failed to record user activity user_id=%s activity=%s",
            user_id,
            activity_log.value if hasattr(activity_log, "value") else activity_log,
        )
        try:
            await db.rollback()
        except Exception:  # nosec B110 -- best-effort rollback cleanup
            pass
