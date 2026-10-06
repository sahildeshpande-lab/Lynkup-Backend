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

DEFAULT_FEATURE_FLAGS: tuple[dict[str, Any], ...] = ()


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
    """No default flags are auto-seeded; feature flags are created and managed by admin."""
    if not DEFAULT_FEATURE_FLAGS:
        return
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
    *,
    actor_user_id: UUID | None = None,
    actor_role: str | None = None,
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
    await db.flush()
    if actor_user_id is not None:
        from apps.administration.services.admin_activity_log_service import create_admin_activity_log

        await create_admin_activity_log(
            db,
            user_id=actor_user_id,
            role=actor_role,
            action="create",
            module="feature_flag",
            record_id=row.id,
            description=f"created platform features to is_enabled {bool(row.is_enabled)}",
            metadata={
                "old": None,
                "new": {
                    "flag": row.key,
                    "enabled": row.is_enabled,
                },
            },
        )
    await db.commit()
    await db.refresh(row)
    return _serialize_flag(row)


async def update_feature_flag(
    payload: FeatureFlagUpdateRequest,
    db: AsyncSession,
    *,
    actor_user_id: UUID | None = None,
    actor_role: str | None = None,
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

    old_enabled = bool(row.is_enabled)
    row.is_enabled = payload.is_enabled
    row.updated_at = utc_now()
    db.add(row)

    if payload.key in ("recommendation", "recommendations"):
        try:
            from apps.profiles.db_models import LearningRecommendationSettings

            rec_settings = (
                await db.execute(select(LearningRecommendationSettings))
            ).scalars().first()
            if rec_settings is not None:
                rec_settings.is_enabled = payload.is_enabled
                rec_settings.updated_at = utc_now()
                if payload.is_enabled is True and rec_settings.cycle_start_date is None:
                    rec_settings.cycle_start_date = utc_now().date()
                db.add(rec_settings)
        except Exception as exc:
            logger.warning(
                "Could not sync learning recommendation settings from feature flag: %s",
                exc,
            )

    if actor_user_id is not None:
        from apps.administration.services.admin_activity_log_service import create_admin_activity_log

        if row.key in ("recommendation", "recommendations", "learning_spotlight"):
            feature_label = "learning spotlight"
        elif row.name:
            feature_label = row.name.lower()
        else:
            feature_label = (row.key or "platform features").replace("_", " ").lower()

        action_word = "enabled" if row.is_enabled else "disabled"
        description = f"{action_word} the {feature_label} feature"

        await create_admin_activity_log(
            db,
            user_id=actor_user_id,
            role=actor_role,
            action="update",
            module="feature_flag",
            record_id=row.id,
            description=description,
            metadata={
                "old": {"enabled": old_enabled},
                "new": {"enabled": bool(row.is_enabled)},
            },
        )
    await db.commit()

    await db.refresh(row)
    return _serialize_flag(row)


async def delete_feature_flag(
    flag_id: UUID,
    db: AsyncSession,
    *,
    actor_user_id: UUID | None = None,
    actor_role: str | None = None,
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
    if actor_user_id is not None:
        from apps.administration.services.admin_activity_log_service import create_admin_activity_log

        await create_admin_activity_log(
            db,
            user_id=actor_user_id,
            role=actor_role,
            action="delete",
            module="feature_flag",
            record_id=row.id,
            description="deleted platform features",
            metadata={
                "old": {"enabled": bool(row.is_enabled), "flag": row.key},
                "new": None,
            },
        )
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
