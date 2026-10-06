from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from common.exceptions import ApiError
from core.auth.config import settings as auth_settings

logger = logging.getLogger(__name__)


async def validate_device_account_limit(
    db: AsyncSession,
    device_id: str | None,
    *,
    user_id: UUID | None = None,
) -> None:
    """
    Validate if the device has reached the maximum allowed accounts limit.

    Raw query counts distinct user_id records associated with device_id in user_installations,
    excluding the current user_id (if existing).

    Raises ApiError("Account limit exceeded") if the limit is exceeded.
    """
    if not device_id or not str(device_id).strip():
        return

    clean_device_id = str(device_id).strip()
    max_accounts = auth_settings.max_accounts_per_device
    if max_accounts <= 0:
        return

    # Avoid `:param::uuid` — asyncpg leaves the second `:name` unbound (syntax error).
    # Branch instead of a NULL-safe cast in raw SQL.
    if user_id is None:
        query = text("""
            SELECT COUNT(DISTINCT user_id)
            FROM user_installations
            WHERE device_id = :device_id
        """)
        params = {"device_id": clean_device_id}
    else:
        query = text("""
            SELECT COUNT(DISTINCT user_id)
            FROM user_installations
            WHERE device_id = :device_id
              AND user_id != :user_id
        """)
        params = {"device_id": clean_device_id, "user_id": user_id}

    result = await db.execute(query, params)
    raw_val = result.scalar() if hasattr(result, "scalar") else 0
    try:
        other_accounts_count = int(raw_val or 0)
    except (TypeError, ValueError):
        other_accounts_count = 0

    if other_accounts_count >= max_accounts:
        logger.warning(
            "Account limit exceeded for device_id=%s. Found %d accounts (max=%d).",
            clean_device_id,
            other_accounts_count,
            max_accounts,
        )
        raise ApiError("The maximum number of accounts allowed on this device has been reached.")
