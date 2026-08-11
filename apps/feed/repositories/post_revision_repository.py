from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.administration.db_models import AdminConfiguration
from apps.administration.db_models.admin_configuration_db_model import utc_now
from apps.administration.schemas import (
    FeatureFlagCreateRequest,
    FeatureFlagUpdateRequest,
)
from common.enums import AdminConfigurationType
from common.exceptions import ApiError

logger = logging.getLogger(__name__)

_FEATURE_FLAG = AdminConfigurationType.FEATURE_FLAG

DEFAULT_FEATURE_FLAGS: tuple[dict[str, Any], ...] = (
    {
        "key": "chat",
        "name": "Chat",
        "description": "In-app messaging between users on the mobile application.",
        "is_enabled": True,
    },
    {
        "key": "recommendations",
        "name": "Recommendations",
        "description": "Learning and connection recommendations on the mobile application.",
        "is_enabled": True,
    },
)


def _serialize_flag(row: AdminConfiguration) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "key": row.key,
        "name": row.name,
        "description": row.description,
        "is_enabled": row.is_enabled,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


async def ensure_default_feature_flags(db: AsyncSession) -> None:
    """Insert default flags if missing. Never overwrites existing rows."""
    result = await db.execute(select(AdminConfiguration.key))
    existing_keys = {key for key in result.scalars().all()}
    now = utc_now()
    created = False
    for default in DEFAULT_FEATURE_FLAGS:
        if default["key"] in existing_keys:
            continue
        db.add(
            AdminConfiguration(
                key=default["key"],
                name=default["name"],
                description=default["description"],
                configuration_type=_FEATURE_FLAG,
                value=None,
                is_enabled=default["is_enabled"],
                created_at=now,
                updated_at=now,
            )
        )
        created = True
    if created:
        await db.commit()
        logger.info("Seeded default feature flags")


async def list_feature_flags(db: AsyncSession) -> dict[str, Any]:
    await ensure_default_feature_flags(db)
    result = await db.execute(
        select(AdminConfiguration)
        .where(AdminConfiguration.configuration_type == _FEATURE_FLAG)
        .order_by(AdminConfiguration.key.asc())
    )
    rows = list(result.scalars().all())
    return {"items": [_serialize_flag(row) for row in rows]}


async def create_feature_flag(
    payload: FeatureFlagCreateRequest,
    db: AsyncSession,
) -> dict[str, Any]:
    existing = (
        await db.execute(
            select(AdminConfiguration).where(AdminConfiguration.key == payload.key)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ApiError("Feature flag already exists")

    now = utc_now()
    row = AdminConfiguration(
        key=payload.key,
        name=payload.name,
        description=payload.description,
        configuration_type=_FEATURE_FLAG,
        value=None,
        is_enabled=payload.is_enabled,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _serialize_flag(row)


async def update_feature_flag(
    payload: FeatureFlagUpdateRequest,
    db: AsyncSession,
) -> dict[str, Any]:
    row = (
        await db.execute(
            select(AdminConfiguration).where(
                AdminConfiguration.key == payload.key,
                AdminConfiguration.configuration_type == _FEATURE_FLAG,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise ApiError("Feature flag not found")

    row.is_enabled = payload.is_enabled
    row.updated_at = utc_now()
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _serialize_flag(row)


async def delete_feature_flag(
    flag_id: UUID,
    db: AsyncSession,
) -> dict[str, Any]:
    """Hard-delete a feature flag by primary key."""
    row = (
        await db.execute(
            select(AdminConfiguration).where(
                AdminConfiguration.id == flag_id,
                AdminConfiguration.configuration_type == _FEATURE_FLAG,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise ApiError("Feature flag not found")

    deleted = _serialize_flag(row)
    await db.delete(row)
    await db.commit()
    return deleted


async def is_feature_enabled(
    db: AsyncSession,
    key: str,
    *,
    default: bool = False,
) -> bool:
    normalized = (key or "").strip().lower()
    if not normalized:
        return default
    value = (
        await db.execute(
            select(AdminConfiguration.is_enabled).where(
                AdminConfiguration.key == normalized,
                AdminConfiguration.configuration_type == _FEATURE_FLAG,
            )
        )
    ).scalar_one_or_none()
    return default if value is None else bool(value)


async def get_feature_flag_by_key(
    db: AsyncSession,
    key: str,
) -> AdminConfiguration | None:
    normalized = (key or "").strip().lower()
    if not normalized:
        return None
    return (
        await db.execute(
            select(AdminConfiguration).where(
                AdminConfiguration.key == normalized,
                AdminConfiguration.configuration_type == _FEATURE_FLAG,
            )
        )
    ).scalar_one_or_none()
