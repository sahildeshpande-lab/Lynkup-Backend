from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.administration.db_models import AdminConfiguration
from common.enums import AdminConfigurationType

THRESHOLD_KEYS: dict[str, str] = {
    "post": "moderation_post_report_threshold",
    "comment": "moderation_comment_report_threshold",
    "user": "moderation_user_report_threshold",
}

THRESHOLD_META: dict[str, dict[str, Any]] = {
    "post": {
        "name": "Post report threshold",
        "description": "Number of reports before a post is auto-flagged for moderation.",
        "default": 10,
    },
    "comment": {
        "name": "Comment report threshold",
        "description": "Number of reports before a comment is soft-deleted.",
        "default": 5,
    },
    "user": {
        "name": "User report threshold",
        "description": "Number of reports before a user is suspended.",
        "default": 10,
    },
}


async def list_threshold_rows(db: AsyncSession) -> list[AdminConfiguration]:
    keys = list(THRESHOLD_KEYS.values())
    result = await db.execute(
        select(AdminConfiguration).where(
            AdminConfiguration.configuration_type == AdminConfigurationType.THRESHOLD,
            AdminConfiguration.key.in_(keys),
        )
    )
    return list(result.scalars().all())


async def get_threshold_row_by_key(
    db: AsyncSession,
    key: str,
) -> AdminConfiguration | None:
    return (
        await db.execute(
            select(AdminConfiguration).where(
                AdminConfiguration.key == key,
                AdminConfiguration.configuration_type == AdminConfigurationType.THRESHOLD,
            )
        )
    ).scalar_one_or_none()
