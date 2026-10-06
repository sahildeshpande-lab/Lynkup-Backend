"""Raw-SQL repost hydration for /feed (Phase 2).

Loads only columns required by ``fetch_feed_posts`` / ``format_repost_item``
and profile enrichment. Does not instantiate Repost or Profile ORM entities.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.feed.repositories.feed_post_hydration import FeedProfileRow

logger = logging.getLogger(__name__)

_REPOST_HYDRATION_SQL = """
SELECT
    r.id AS repost_id,
    r.created_at AS repost_created_at,
    pr.user_id AS profile_user_id,
    pr.first_name AS profile_first_name,
    pr.last_name AS profile_last_name,
    pr.profile_photo_url AS profile_photo_url,
    pr.profile_visibility AS profile_visibility,
    pr.university_id AS profile_university_id,
    pr.profile_interests_id AS profile_interests_id,
    pr.bio AS profile_bio,
    pr.major AS profile_major,
    pr.minor AS profile_minor,
    pr.edu_level AS profile_edu_level
FROM reposts r
JOIN profiles pr ON pr.id = r.profile_id
WHERE r.id IN :repost_ids
"""


@dataclass
class FeedRepostRow:
    """Lightweight repost shape: id + created_at (fields read by feed assembly)."""

    id: UUID
    created_at: Any


def _perf_ms(started_at: float) -> float:
    return (time.perf_counter() - started_at) * 1000.0


def _map_repost_rows(
    rows: list[Any],
) -> dict[UUID, tuple[FeedRepostRow, FeedProfileRow]]:
    """Build repost_id -> (repost, reposter_profile) map from raw join rows."""
    mapped: dict[UUID, tuple[FeedRepostRow, FeedProfileRow]] = {}
    for row in rows:
        repost_id = row["repost_id"]
        if repost_id in mapped:
            continue
        mapped[repost_id] = (
            FeedRepostRow(
                id=repost_id,
                created_at=row["repost_created_at"],
            ),
            FeedProfileRow(
                user_id=row["profile_user_id"],
                first_name=row["profile_first_name"],
                last_name=row["profile_last_name"],
                profile_photo_url=row["profile_photo_url"],
                profile_visibility=row["profile_visibility"],
                university_id=row["profile_university_id"],
                profile_interests_id=row["profile_interests_id"] or [],
                bio=row["profile_bio"],
                major=row["profile_major"],
                minor=row["profile_minor"],
                edu_level=row["profile_edu_level"],
            ),
        )
    return mapped


async def hydrate_feed_reposts_raw(
    db: AsyncSession,
    repost_ids: set[UUID] | list[UUID],
) -> dict[UUID, tuple[FeedRepostRow, FeedProfileRow]]:
    """Batch-hydrate reposts + reposter profiles via one raw SQL statement.

    Returns a map keyed by repost id. Callers must associate via feed event
    ``repost_id`` — this map does not define feed order.
    """
    unique_ids = list({rid for rid in repost_ids if rid is not None})
    if not unique_ids:
        logger.info(
            "[FEED_PERF] raw_repost_hydration_total=0.00ms requested_reposts=0 "
            "rows=0 hydrated_reposts=0"
        )
        return {}

    stmt = text(_REPOST_HYDRATION_SQL).bindparams(
        bindparam("repost_ids", expanding=True)
    )

    total_started = time.perf_counter()
    sql_started = time.perf_counter()
    result = await db.execute(stmt, {"repost_ids": unique_ids})
    rows = result.mappings().all()
    sql_ms = _perf_ms(sql_started)

    map_started = time.perf_counter()
    mapped = _map_repost_rows(rows)
    map_ms = _perf_ms(map_started)
    total_ms = _perf_ms(total_started)

    logger.info(
        "[FEED_PERF] raw_repost_hydration_sql=%.2fms requested_reposts=%s rows=%s",
        sql_ms,
        len(unique_ids),
        len(rows),
    )
    logger.info(
        "[FEED_PERF] raw_repost_hydration_mapping=%.2fms hydrated_reposts=%s",
        map_ms,
        len(mapped),
    )
    logger.info(
        "[FEED_PERF] raw_repost_hydration_total=%.2fms requested_reposts=%s rows=%s "
        "hydrated_reposts=%s sql_statements=1",
        total_ms,
        len(unique_ids),
        len(rows),
        len(mapped),
    )
    return mapped
