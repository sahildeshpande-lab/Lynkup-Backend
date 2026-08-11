from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.administration.db_models import AdminConfiguration
from apps.administration.db_models.admin_configuration_db_model import utc_now
from apps.threshold_configuration.repositories import (
    THRESHOLD_KEYS,
    THRESHOLD_META,
    get_threshold_row_by_key,
    list_threshold_rows,
)
from apps.threshold_configuration.schemas import (
    ModerationThresholdsData,
    UpdateModerationThresholdsRequest,
)
from common.enums import AdminConfigurationType
from common.exceptions import ApiError

logger = logging.getLogger(__name__)


def _threshold_from_value(value: dict[str, Any] | None, *, fallback: int) -> int:
    if not isinstance(value, dict):
        return fallback
    raw = value.get("threshold", fallback)
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


async def ensure_default_thresholds(db: AsyncSession) -> None:
    """Insert default moderation threshold configs if missing."""
    result = await db.execute(select(AdminConfiguration.key))
    existing_keys = {key for key in result.scalars().all()}
    now = utc_now()
    created = False
    for field, key in THRESHOLD_KEYS.items():
        if key in existing_keys:
            continue
        meta = THRESHOLD_META[field]
        db.add(
            AdminConfiguration(
                key=key,
                name=meta["name"],
                description=meta["description"],
                configuration_type=AdminConfigurationType.THRESHOLD,
                value={"threshold": meta["default"]},
                is_enabled=True,
                created_at=now,
                updated_at=now,
            )
        )
        created = True
    if created:
        await db.commit()
        logger.info("Seeded default moderation thresholds")


async def get_moderation_thresholds(db: AsyncSession) -> ModerationThresholdsData:
    await ensure_default_thresholds(db)
    rows = await list_threshold_rows(db)
    by_key = {row.key: row for row in rows}

    values: dict[str, int] = {}
    for field, key in THRESHOLD_KEYS.items():
        row = by_key.get(key)
        fallback = int(THRESHOLD_META[field]["default"])
        values[field] = (
            _threshold_from_value(row.value, fallback=fallback)
            if row is not None
            else fallback
        )
    return ModerationThresholdsData(**values)


async def update_moderation_thresholds(
    payload: UpdateModerationThresholdsRequest,
    db: AsyncSession,
) -> ModerationThresholdsData:
    await ensure_default_thresholds(db)
    now = utc_now()
    updates = {
        field: getattr(payload, field)
        for field in ("post", "comment", "user")
        if getattr(payload, field) is not None
    }

    for field, threshold in updates.items():
        key = THRESHOLD_KEYS[field]
        row = await get_threshold_row_by_key(db, key)
        if row is None:
            raise ApiError(f"Threshold configuration '{key}' not found")
        row.value = {"threshold": int(threshold)}
        row.updated_at = now
        db.add(row)

    await db.commit()
    return await get_moderation_thresholds(db)
