"""Combined post + repost raw-SQL hydration for /feed (Phase 7).

One round trip returning independent JSON payloads — never joins post rows
to repost rows (avoids Cartesian multiplication with attachments).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession

from apps.feed.repositories.feed_post_hydration import (
    FeedPostRow,
    FeedProfileRow,
    _group_hydration_rows,
)
from apps.feed.repositories.feed_repost_hydration import (
    FeedRepostRow,
    _map_repost_rows,
)

logger = logging.getLogger(__name__)

# Independent subqueries → JSON columns. Empty arrays are safe with ANY().
_COMBINED_HYDRATION_SQL = """
SELECT
    COALESCE(
        (
            SELECT json_agg(row_to_json(p) ORDER BY p.attachment_id ASC NULLS LAST)
            FROM (
                SELECT
                    posts.id AS post_id,
                    posts.author_user_id AS post_author_user_id,
                    posts.state AS post_state,
                    posts.revision_number AS post_revision_number,
                    posts.content AS post_content,
                    posts.created_at AS post_created_at,
                    posts.updated_at AS post_updated_at,
                    posts.is_edited AS post_is_edited,
                    posts.like_count AS post_like_count,
                    posts.repost_count AS post_repost_count,
                    posts.share_count AS post_share_count,
                    posts.comment_count AS post_comment_count,
                    posts.is_moderator_reviewed AS post_is_moderator_reviewed,
                    posts.reviewed_at AS post_reviewed_at,
                    posts.moderator_id AS post_moderator_id,
                    posts.moderation_notes AS post_moderation_notes,
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
                    pr.edu_level AS profile_edu_level,
                    pa.id AS attachment_id,
                    ma.id AS media_id,
                    ma.key AS media_key,
                    ma.type AS media_type,
                    ma.original_filename AS media_original_filename,
                    ma.mime_type AS media_mime_type,
                    ma.file_size AS media_file_size
                FROM posts
                JOIN profiles pr ON pr.user_id = posts.author_user_id
                LEFT JOIN post_attachments pa ON pa.post_id = posts.id
                LEFT JOIN media_assets ma ON ma.id = pa.media_asset_id
                WHERE cardinality(CAST(:post_ids AS uuid[])) > 0
                  AND posts.id = ANY(CAST(:post_ids AS uuid[]))
            ) p
        ),
        '[]'::json
    ) AS post_rows,
    COALESCE(
        (
            SELECT json_agg(row_to_json(r))
            FROM (
                SELECT
                    reposts.id AS repost_id,
                    reposts.created_at AS repost_created_at,
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
                FROM reposts
                JOIN profiles pr ON pr.id = reposts.profile_id
                WHERE cardinality(CAST(:repost_ids AS uuid[])) > 0
                  AND reposts.id = ANY(CAST(:repost_ids AS uuid[]))
            ) r
        ),
        '[]'::json
    ) AS repost_rows
"""


@dataclass(frozen=True)
class FeedHydrationMaps:
    posts: dict[UUID, tuple[FeedPostRow, FeedProfileRow]]
    reposts: dict[UUID, tuple[FeedRepostRow, FeedProfileRow]]


def _perf_ms(started_at: float) -> float:
    return (time.perf_counter() - started_at) * 1000.0


def _coerce_json_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    if isinstance(value, list):
        return value
    return list(value)


def _as_uuid_maybe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return value


def _normalize_post_row(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize JSON keys/types for ``_group_hydration_rows``."""
    return {
        "post_id": _as_uuid_maybe(row.get("post_id")),
        "post_author_user_id": _as_uuid_maybe(row.get("post_author_user_id")),
        "post_state": row.get("post_state"),
        "post_revision_number": row.get("post_revision_number"),
        "post_content": row.get("post_content"),
        "post_created_at": row.get("post_created_at"),
        "post_updated_at": row.get("post_updated_at"),
        "post_is_edited": row.get("post_is_edited"),
        "post_like_count": row.get("post_like_count"),
        "post_repost_count": row.get("post_repost_count"),
        "post_share_count": row.get("post_share_count"),
        "post_comment_count": row.get("post_comment_count"),
        "post_is_moderator_reviewed": row.get("post_is_moderator_reviewed"),
        "post_reviewed_at": row.get("post_reviewed_at"),
        "post_moderator_id": _as_uuid_maybe(row.get("post_moderator_id")),
        "post_moderation_notes": row.get("post_moderation_notes"),
        "profile_user_id": _as_uuid_maybe(row.get("profile_user_id")),
        "profile_first_name": row.get("profile_first_name"),
        "profile_last_name": row.get("profile_last_name"),
        "profile_photo_url": row.get("profile_photo_url"),
        "profile_visibility": row.get("profile_visibility"),
        "profile_university_id": _as_uuid_maybe(row.get("profile_university_id")),
        "profile_interests_id": row.get("profile_interests_id") or [],
        "profile_bio": row.get("profile_bio"),
        "profile_major": row.get("profile_major"),
        "profile_minor": row.get("profile_minor"),
        "profile_edu_level": row.get("profile_edu_level"),
        "attachment_id": _as_uuid_maybe(row.get("attachment_id")),
        "media_id": _as_uuid_maybe(row.get("media_id")),
        "media_key": row.get("media_key"),
        "media_type": row.get("media_type"),
        "media_original_filename": row.get("media_original_filename"),
        "media_mime_type": row.get("media_mime_type"),
        "media_file_size": row.get("media_file_size"),
    }


def _normalize_repost_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "repost_id": _as_uuid_maybe(row.get("repost_id")),
        "repost_created_at": row.get("repost_created_at"),
        "profile_user_id": _as_uuid_maybe(row.get("profile_user_id")),
        "profile_first_name": row.get("profile_first_name"),
        "profile_last_name": row.get("profile_last_name"),
        "profile_photo_url": row.get("profile_photo_url"),
        "profile_visibility": row.get("profile_visibility"),
        "profile_university_id": _as_uuid_maybe(row.get("profile_university_id")),
        "profile_interests_id": row.get("profile_interests_id") or [],
        "profile_bio": row.get("profile_bio"),
        "profile_major": row.get("profile_major"),
        "profile_minor": row.get("profile_minor"),
        "profile_edu_level": row.get("profile_edu_level"),
    }


async def hydrate_feed_posts_and_reposts_raw(
    db: AsyncSession,
    post_ids: set[UUID] | list[UUID],
    repost_ids: set[UUID] | list[UUID],
) -> FeedHydrationMaps:
    """Hydrate posts and reposts in one SQL round trip."""
    unique_posts = sorted({pid for pid in post_ids if pid is not None}, key=str)
    unique_reposts = sorted({rid for rid in repost_ids if rid is not None}, key=str)

    if not unique_posts and not unique_reposts:
        logger.info(
            "[FEED_PERF] combined_hydration_total=0.00ms posts=0 reposts=0 "
            "sql_statements=0"
        )
        return FeedHydrationMaps(posts={}, reposts={})

    stmt = text(_COMBINED_HYDRATION_SQL).bindparams(
        bindparam("post_ids", type_=ARRAY(PGUUID(as_uuid=True))),
        bindparam("repost_ids", type_=ARRAY(PGUUID(as_uuid=True))),
    )

    total_started = time.perf_counter()
    execute_started = time.perf_counter()
    result = await db.execute(
        stmt,
        {"post_ids": unique_posts, "repost_ids": unique_reposts},
    )
    execute_ms = _perf_ms(execute_started)

    fetch_started = time.perf_counter()
    row = result.mappings().one()
    fetch_ms = _perf_ms(fetch_started)

    map_started = time.perf_counter()
    post_raw = [
        _normalize_post_row(r)
        for r in _coerce_json_list(row["post_rows"])
        if isinstance(r, dict)
    ]
    repost_raw = [
        _normalize_repost_row(r)
        for r in _coerce_json_list(row["repost_rows"])
        if isinstance(r, dict)
    ]
    posts_map = _group_hydration_rows(post_raw) if post_raw else {}
    reposts_map = _map_repost_rows(repost_raw) if repost_raw else {}
    map_ms = _perf_ms(map_started)
    total_ms = _perf_ms(total_started)

    attachment_count = sum(len(post.attachments) for post, _ in posts_map.values())
    logger.info(
        "[FEED_PERF] combined_hydration_sql=%.2fms requested_posts=%s "
        "requested_reposts=%s",
        execute_ms,
        len(unique_posts),
        len(unique_reposts),
    )
    logger.info(
        "[FEED_PERF] combined_hydration_fetch=%.2fms post_rows=%s repost_rows=%s",
        fetch_ms,
        len(post_raw),
        len(repost_raw),
    )
    logger.info(
        "[FEED_PERF] combined_hydration_mapping=%.2fms hydrated_posts=%s "
        "hydrated_reposts=%s attachments=%s",
        map_ms,
        len(posts_map),
        len(reposts_map),
        attachment_count,
    )
    logger.info(
        "[FEED_PERF] combined_hydration_total=%.2fms sql_statements=1",
        total_ms,
    )
    return FeedHydrationMaps(posts=posts_map, reposts=reposts_map)
