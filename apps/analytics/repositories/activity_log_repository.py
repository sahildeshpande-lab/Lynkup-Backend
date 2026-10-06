from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from apps.analytics.db_models import UserActivityLog
from common.enums import UserActivityLogType


async def create_user_activity_log(
    db: AsyncSession,
    user_id: UUID,
    activity_log: UserActivityLogType,
) -> UserActivityLog:
    record = UserActivityLog(user_id=user_id, activity_log=activity_log)
    db.add(record)
    await db.flush()
    return record
