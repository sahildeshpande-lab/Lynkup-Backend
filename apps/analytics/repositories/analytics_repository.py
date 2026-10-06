from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.analytics.config import settings as analytics_settings


async def fetch_admin_analytics_dashboard(
    db: AsyncSession,
    *,
    days: int,
    distribution_type: str | None,
    timezone: str | None = None,
) -> dict[str, Any]:
    """Call the PostgreSQL analytics dashboard function and return parsed JSON."""
    resolved_timezone = (timezone or analytics_settings.timezone or "America/New_York").strip()
    result = await db.execute(
        text(
            "SELECT get_admin_analytics_dashboard("
            ":days, :distribution_type, :timezone"
            ") AS payload"
        ),
        {
            "days": days,
            "distribution_type": distribution_type,
            "timezone": resolved_timezone,
        },
    )
    payload = result.scalar_one()
    if payload is None:
        return {}
    if isinstance(payload, dict):
        return payload
    # asyncpg / psycopg may return JSON as str depending on driver settings
    import json

    if isinstance(payload, str):
        return json.loads(payload)
    return dict(payload)
