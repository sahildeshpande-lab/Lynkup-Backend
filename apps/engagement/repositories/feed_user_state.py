"""Combined feed user-state: engagement flags + latest reactions (Phase 3).

One parameterized SQL round trip. Independent CTEs aggregated to JSON —
never joined engagement rows to reaction rows (avoids row multiplication).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.engagement.repositories.engagement_repository import PostEngagementFlags
from apps.engagement.schemas import PostReactionsGrouped, PostReactorProfile
from common.enums import ReactionType
from core.images import generate_profile_image_url

logger = logging.getLogger(__name__)

# Independent CTEs; results returned as JSON columns (no engagement ⨯ reactions join).
_FEED_USER_STATE_SQL = """
WITH viewer_reactions AS (
    SELECT
        pr.post_id,
        pr.reaction_type::text AS reaction_type
    FROM post_reactions pr
    WHERE pr.user_id = :user_id
      AND pr.post_id IN :post_ids
),
viewer_bookmarks AS (
    SELECT b.post_id
    FROM bookmarks b
    WHERE b.user_id = :user_id
      AND b.post_id IN :post_ids
),
viewer_reposts AS (
    SELECT r.post_id
    FROM reposts r
    JOIN profiles p ON p.id = r.profile_id
    WHERE p.user_id = :user_id
      AND r.post_id IN :post_ids
      AND r.is_deleted = false
),
ranked_reactions AS (
    SELECT
        pr.post_id,
        pr.reaction_type::text AS reaction_type,
        pr.created_at,
        p.user_id AS profile_user_id,
        p.first_name,
        p.last_name,
        p.profile_photo_url,
        p.bio,
        u.name AS university_name,
        row_number() OVER (
            PARTITION BY pr.post_id, pr.reaction_type
            ORDER BY pr.created_at DESC
        ) AS rn
    FROM post_reactions pr
    LEFT JOIN profiles p ON p.user_id = pr.user_id
    LEFT JOIN universities u ON u.id = p.university_id
    WHERE pr.post_id IN :post_ids
),
latest_reactions AS (
    SELECT
        post_id,
        reaction_type,
        created_at,
        profile_user_id,
        first_name,
        last_name,
        profile_photo_url,
        bio,
        university_name
    FROM ranked_reactions
    WHERE rn <= :per_type_limit
)
SELECT
    COALESCE(
        (
            SELECT json_agg(
                json_build_object(
                    'post_id', post_id,
                    'reaction_type', reaction_type
                )
            )
            FROM viewer_reactions
        ),
        '[]'::json
    ) AS viewer_reactions,
    COALESCE(
        (
            SELECT json_agg(post_id)
            FROM viewer_bookmarks
        ),
        '[]'::json
    ) AS viewer_bookmarks,
    COALESCE(
        (
            SELECT json_agg(post_id)
            FROM viewer_reposts
        ),
        '[]'::json
    ) AS viewer_reposts,
    COALESCE(
        (
            SELECT json_agg(
                json_build_object(
                    'post_id', post_id,
                    'reaction_type', reaction_type,
                    'created_at', created_at,
                    'profile_user_id', profile_user_id,
                    'first_name', first_name,
                    'last_name', last_name,
                    'profile_photo_url', profile_photo_url,
                    'bio', bio,
                    'university_name', university_name
                )
                ORDER BY post_id, reaction_type, created_at DESC
            )
            FROM latest_reactions
        ),
        '[]'::json
    ) AS latest_reactions
"""


@dataclass(frozen=True)
class FeedUserState:
    engagement: PostEngagementFlags
    latest_reactions: dict[UUID, PostReactionsGrouped]


def _format_reaction_type(reaction_type: ReactionType | None) -> str | None:
    if reaction_type is None:
        return None
    return reaction_type.value.upper()


def _empty_reactions_group() -> PostReactionsGrouped:
    return PostReactionsGrouped()


def _group_reactor_profiles(reactors: list[PostReactorProfile]) -> PostReactionsGrouped:
    grouped = _empty_reactions_group()
    for reactor in reactors:
        bucket = getattr(grouped, reactor.reaction_type, None)
        if bucket is not None:
            bucket.append(reactor)
    return grouped


def _perf_ms(started_at: float) -> float:
    return (time.perf_counter() - started_at) * 1000.0


def _as_uuid(value: Any) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    text_value = str(value)
    if text_value.endswith("Z"):
        text_value = text_value[:-1] + "+00:00"
    return datetime.fromisoformat(text_value)


def _parse_reaction_type(value: Any) -> ReactionType | None:
    if value is None:
        return None
    if isinstance(value, ReactionType):
        return value
    normalized = str(value).strip().lower()
    try:
        return ReactionType(normalized)
    except ValueError:
        return None


def _coerce_json_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        import json

        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    if isinstance(value, list):
        return value
    return list(value)


def map_feed_user_state_payload(
    *,
    post_ids: list[UUID],
    viewer_reactions: Any,
    viewer_bookmarks: Any,
    viewer_reposts: Any,
    latest_reactions: Any,
) -> FeedUserState:
    """Map raw JSON payload columns into engagement flags + grouped reactions."""
    reaction_rows = _coerce_json_list(viewer_reactions)
    bookmark_rows = _coerce_json_list(viewer_bookmarks)
    repost_rows = _coerce_json_list(viewer_reposts)
    latest_rows = _coerce_json_list(latest_reactions)

    user_reactions: list[tuple[UUID, ReactionType]] = []
    for row in reaction_rows:
        if not isinstance(row, dict):
            continue
        reaction_type = _parse_reaction_type(row.get("reaction_type"))
        post_id = row.get("post_id")
        if reaction_type is None or post_id is None:
            continue
        user_reactions.append((_as_uuid(post_id), reaction_type))

    bookmarked_post_ids = frozenset(
        _as_uuid(item if not isinstance(item, dict) else item.get("post_id"))
        for item in bookmark_rows
        if item is not None and not (isinstance(item, dict) and item.get("post_id") is None)
    )
    reposted_post_ids = frozenset(
        _as_uuid(item if not isinstance(item, dict) else item.get("post_id"))
        for item in repost_rows
        if item is not None and not (isinstance(item, dict) and item.get("post_id") is None)
    )

    engagement = PostEngagementFlags(
        user_reactions=tuple(user_reactions),
        reposted_post_ids=reposted_post_ids,
        bookmarked_post_ids=bookmarked_post_ids,
    )

    reactors_by_post: dict[UUID, list[PostReactorProfile]] = {pid: [] for pid in post_ids}
    for row in latest_rows:
        if not isinstance(row, dict):
            continue
        post_id_raw = row.get("post_id")
        reaction_type = _parse_reaction_type(row.get("reaction_type"))
        created_at_raw = row.get("created_at")
        if post_id_raw is None or reaction_type is None or created_at_raw is None:
            continue
        post_id = _as_uuid(post_id_raw)
        profile_user_id = row.get("profile_user_id")
        photo_key = row.get("profile_photo_url")
        reactor = PostReactorProfile(
            profile_id=_as_uuid(profile_user_id) if profile_user_id is not None else None,
            first_name=row.get("first_name"),
            last_name=row.get("last_name"),
            profilePhoto_url=(
                generate_profile_image_url(photo_key) if photo_key else None
            ),
            bio=row.get("bio"),
            reaction_type=_format_reaction_type(reaction_type),
            reacted_at=_as_datetime(created_at_raw),
        )
        reactors_by_post.setdefault(post_id, []).append(reactor)

    latest: dict[UUID, PostReactionsGrouped] = {
        post_id: _group_reactor_profiles(reactors_by_post.get(post_id, []))
        for post_id in post_ids
    }
    return FeedUserState(engagement=engagement, latest_reactions=latest)


async def fetch_feed_user_state(
    db: AsyncSession,
    user_id: UUID,
    post_ids: list[UUID],
    *,
    per_type_limit: int = 3,
) -> FeedUserState:
    """Load viewer engagement flags and latest reactors in one SQL round trip."""
    if not post_ids:
        logger.info(
            "[FEED_PERF] user_state_total=0.00ms post_count=0 engagement_rows=0 "
            "reaction_rows=0 sql_statements=0"
        )
        return FeedUserState(
            engagement=PostEngagementFlags.empty(),
            latest_reactions={},
        )

    # Preserve call-order keys (duplicates possible across repost events).
    ordered_ids = list(post_ids)

    stmt = text(_FEED_USER_STATE_SQL).bindparams(
        bindparam("post_ids", expanding=True),
        bindparam("user_id"),
        bindparam("per_type_limit"),
    )

    total_started = time.perf_counter()
    execute_started = time.perf_counter()
    result = await db.execute(
        stmt,
        {
            "post_ids": list({pid for pid in ordered_ids}),
            "user_id": user_id,
            "per_type_limit": per_type_limit,
        },
    )
    execute_ms = _perf_ms(execute_started)

    fetch_started = time.perf_counter()
    row = result.mappings().one()
    fetch_ms = _perf_ms(fetch_started)

    map_started = time.perf_counter()
    mapped = map_feed_user_state_payload(
        post_ids=ordered_ids,
        viewer_reactions=row["viewer_reactions"],
        viewer_bookmarks=row["viewer_bookmarks"],
        viewer_reposts=row["viewer_reposts"],
        latest_reactions=row["latest_reactions"],
    )
    # Ensure unique post keys in latest map (ordered_ids may repeat).
    unique_latest = {
        pid: mapped.latest_reactions.get(pid, _empty_reactions_group())
        for pid in dict.fromkeys(ordered_ids)
    }
    mapped = FeedUserState(
        engagement=mapped.engagement,
        latest_reactions=unique_latest,
    )
    map_ms = _perf_ms(map_started)
    total_ms = _perf_ms(total_started)

    engagement_row_count = (
        len(mapped.engagement.user_reactions)
        + len(mapped.engagement.bookmarked_post_ids)
        + len(mapped.engagement.reposted_post_ids)
    )
    reaction_row_count = sum(
        len(group.LIKE)
        + len(group.CELEBRATE)
        + len(group.INSIGHTFUL)
        + len(group.SUPPORT)
        + len(group.CURIOUS)
        for group in mapped.latest_reactions.values()
    )

    logger.info(
        "[FEED_PERF] user_state_sql_execute=%.2fms post_count=%s",
        execute_ms,
        len(unique_latest),
    )
    logger.info(
        "[FEED_PERF] user_state_fetch=%.2fms",
        fetch_ms,
    )
    logger.info(
        "[FEED_PERF] user_state_mapping=%.2fms engagement_rows=%s reaction_rows=%s",
        map_ms,
        engagement_row_count,
        reaction_row_count,
    )
    logger.info(
        "[FEED_PERF] user_state_total=%.2fms post_count=%s engagement_rows=%s "
        "reaction_rows=%s sql_statements=1",
        total_ms,
        len(unique_latest),
        engagement_row_count,
        reaction_row_count,
    )
    return mapped
