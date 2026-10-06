from __future__ import annotations

import logging
import time
from datetime import datetime
from uuid import UUID

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.connections.db_models import Block
from apps.feed.repositories.feed_combined_hydration import (
    hydrate_feed_posts_and_reposts_raw,
)
from apps.profiles.db_models import Profile

logger = logging.getLogger(__name__)


def _perf_ms(started_at: float) -> float:
    return (time.perf_counter() - started_at) * 1000.0

#  to check post visibility
def _visible_author_sql(alias: str) -> str:
    """SQL fragment for authors whose content may appear publicly.

    Grace-period ``deleting`` users keep content visible until permanent purge.
    """
    return f"""
        {alias}.status::text NOT IN ('suspended', 'banned')
        AND (
            ({alias}.is_deleted = false AND {alias}.deleted_at IS NULL)
            OR {alias}.status::text = 'deleting'
        )
    """

#  to check account status
def _not_blocked_sql(author_user_expr: str) -> str:  # nosec B608 -- column name interpolation, not user input
    return f"""
        NOT EXISTS (
            SELECT 1
            FROM blocks b
            WHERE b.is_active = true
              AND (
                    (b.blocker_user_id = :current_user AND b.blocked_user_id = {author_user_expr})
                 OR (b.blocked_user_id = :current_user AND b.blocker_user_id = {author_user_expr})
              )
        )
    """

# to check for connected user
def _connected_sql(author_user_expr: str) -> str:  # nosec B608 -- column name interpolation, not user input
    return f"""
        EXISTS (
            SELECT 1
            FROM connections c
            WHERE c.is_active = true
              AND (
                    (c.user_low_id = :current_user AND c.user_high_id = {author_user_expr})
                 OR (c.user_high_id = :current_user AND c.user_low_id = {author_user_expr})
              )
        )
    """


def _academic_match_sql(profile_alias: str) -> str:
    """1 when viewer shares university, major, or minor with the profile; else 0."""
    return f"""
        CASE
            WHEN (
                (
                    me.university_id IS NOT NULL
                    AND {profile_alias}.university_id = me.university_id
                )
                OR (
                    me.major IS NOT NULL
                    AND btrim(me.major) <> ''
                    AND {profile_alias}.major IS NOT NULL
                    AND lower(btrim({profile_alias}.major)) = lower(btrim(me.major))
                )
                OR (
                    me.minor IS NOT NULL
                    AND btrim(me.minor) <> ''
                    AND {profile_alias}.minor IS NOT NULL
                    AND lower(btrim({profile_alias}.minor)) = lower(btrim(me.minor))
                )
            )
            THEN 1
            ELSE 0
        END
    """


def _feed_match_sql(profile_alias: str, *user_exprs: str) -> str:
    """1 when academic match or the viewer is connected to any of the given users."""
    connected = " OR ".join(_connected_sql(expr) for expr in user_exprs) if user_exprs else "false"
    return f"""
        CASE
            WHEN {_academic_match_sql(profile_alias)} = 1
              OR ({connected})
            THEN 1
            ELSE 0
        END
    """


def _visibility_sql(author_profile_alias: str, author_user_expr: str) -> str:
    """Existing feed visibility: public always, private/connections_only if connected."""
    return f"""
        (
            {author_profile_alias}.profile_visibility::text = 'public'
            OR (
                {author_profile_alias}.profile_visibility::text IN ('private', 'connections_only')
                AND {_connected_sql(author_user_expr)}
            )
        )
    """


# Prefer academic matches (uni/major/minor) and connected users. If none match,
# show all visible posts. Original posts match the author; reposts match the
# reposter or original author.
_FEED_EVENTS_SQL = f"""
WITH ranked_posts AS (
    SELECT
        p.id AS post_id,
        CAST(NULL AS uuid) AS repost_id,
        p.created_at AS created_at,
        'post' AS event_type,
        (
            p.like_count * 3
            + p.comment_count *2 
            + p.repost_count
        ) AS engagement_score,
        {_feed_match_sql("author_profile", "p.author_user_id")} AS is_match
    FROM posts p
    JOIN profiles author_profile
        ON author_profile.user_id = p.author_user_id
    JOIN users author_user
        ON author_user.id = p.author_user_id
    LEFT JOIN profiles me
        ON me.user_id = :current_user
    WHERE p.state::text IN ('published', 'reinstate')
      AND p.author_user_id <> :current_user
      AND {_visible_author_sql("author_user")}
      AND {_visibility_sql("author_profile", "p.author_user_id")}
      AND {_not_blocked_sql("p.author_user_id")}

    UNION ALL

    SELECT
        p.id AS post_id,
        r.id AS repost_id,
        r.created_at AS created_at,
        'repost' AS event_type,
        (
            p.like_count * 3
            + p.comment_count * 2
            + p.repost_count 
        ) AS engagement_score, 
        {_feed_match_sql("reposter_profile", "reposter_profile.user_id", "p.author_user_id")} AS is_match
    FROM reposts r
    JOIN profiles reposter_profile
        ON reposter_profile.id = r.profile_id
    JOIN users reposter_user
        ON reposter_user.id = reposter_profile.user_id
    JOIN posts p
        ON p.id = r.post_id
    JOIN profiles author_profile
        ON author_profile.user_id = p.author_user_id
    JOIN users author_user
        ON author_user.id = p.author_user_id
    LEFT JOIN profiles me
        ON me.user_id = :current_user
    WHERE p.state::text IN ('published', 'reinstate')
      AND reposter_profile.user_id <> :current_user
      AND p.author_user_id <> :current_user
      AND r.is_deleted = false
      AND {_visible_author_sql("reposter_user")}
      AND {_visible_author_sql("author_user")}
      AND {_visibility_sql("reposter_profile", "reposter_profile.user_id")}
      AND {_visibility_sql("author_profile", "p.author_user_id")}
      AND {_not_blocked_sql("reposter_profile.user_id")}
      AND {_not_blocked_sql("p.author_user_id")}
),
feed_stats AS (
    SELECT COALESCE(MAX(is_match), 0) AS max_match FROM ranked_posts
)
SELECT
    ranked_posts.post_id,
    ranked_posts.repost_id,
    ranked_posts.created_at,
    ranked_posts.event_type,
    ranked_posts.engagement_score
FROM ranked_posts
CROSS JOIN feed_stats
WHERE
    (feed_stats.max_match = 0 OR ranked_posts.is_match > 0)
    AND (
        CAST(:has_cursor AS boolean) = false
        OR ranked_posts.engagement_score < :last_engagement_score
        OR (
            ranked_posts.engagement_score = :last_engagement_score
            AND ranked_posts.created_at < :last_created_at
        )
        OR (
            ranked_posts.engagement_score = :last_engagement_score
            AND ranked_posts.created_at = :last_created_at
            AND ranked_posts.post_id < :last_post_id
        )
    )
ORDER BY
    ranked_posts.engagement_score DESC ,
    ranked_posts.created_at DESC ,
    ranked_posts.post_id DESC 
 

"""
   # created_at DESC,
    # post_id DESC

# get user profile
async def fetch_viewer_profile(db: AsyncSession, user_id: UUID) -> Profile | None:
    stmt = select(Profile).where(Profile.user_id == user_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def fetch_blocked_user_ids(db: AsyncSession, user_id: UUID) -> set[UUID]:
    stmt = select(Block.blocker_user_id, Block.blocked_user_id).where(
        Block.is_active == True,  # noqa: E712
        or_(
            Block.blocker_user_id == user_id,
            Block.blocked_user_id == user_id,
        ),
    )
    res = await db.execute(stmt)
    blocked_ids: set[UUID] = set()
    for blocker, blocked in res.all():
        if blocker == user_id:
            blocked_ids.add(blocked)
        else:
            blocked_ids.add(blocker)
    return blocked_ids


_FEED_COUNT_SQL = f"""
WITH ranked_posts AS (
    SELECT
        p.id AS post_id,
        {_feed_match_sql("author_profile", "p.author_user_id")} AS is_match
    FROM posts p
    JOIN profiles author_profile
        ON author_profile.user_id = p.author_user_id
    JOIN users author_user
        ON author_user.id = p.author_user_id
    LEFT JOIN profiles me
        ON me.user_id = :current_user
    WHERE p.state::text IN ('published', 'reinstate')
      AND p.author_user_id <> :current_user
      AND {_visible_author_sql("author_user")}
      AND {_visibility_sql("author_profile", "p.author_user_id")}
      AND {_not_blocked_sql("p.author_user_id")}

    UNION ALL

    SELECT
        p.id AS post_id,
        {_feed_match_sql("reposter_profile", "reposter_profile.user_id", "p.author_user_id")} AS is_match
    FROM reposts r
    JOIN profiles reposter_profile
        ON reposter_profile.id = r.profile_id
    JOIN users reposter_user
        ON reposter_user.id = reposter_profile.user_id
    JOIN posts p
        ON p.id = r.post_id
    JOIN profiles author_profile
        ON author_profile.user_id = p.author_user_id
    JOIN users author_user
        ON author_user.id = p.author_user_id
    LEFT JOIN profiles me
        ON me.user_id = :current_user
    WHERE p.state::text IN ('published', 'reinstate')
      AND reposter_profile.user_id <> :current_user
      AND p.author_user_id <> :current_user
      AND r.is_deleted = false
      AND {_visible_author_sql("reposter_user")}
      AND {_visible_author_sql("author_user")}
      AND {_visibility_sql("reposter_profile", "reposter_profile.user_id")}
      AND {_visibility_sql("author_profile", "p.author_user_id")}
      AND {_not_blocked_sql("reposter_profile.user_id")}
      AND {_not_blocked_sql("p.author_user_id")}
),
feed_stats AS (
    SELECT COALESCE(MAX(is_match), 0) AS max_match FROM ranked_posts
)
SELECT COUNT(*)
FROM ranked_posts
CROSS JOIN feed_stats
WHERE feed_stats.max_match = 0 OR ranked_posts.is_match > 0
"""

# count the post
async def count_feed_posts(
    db: AsyncSession,
    current_user_id: UUID,
    viewer_profile: Profile | None,
    connected_author_ids: set[UUID],
) -> int:
    """Count feed events using the same match/visibility rules as fetch (no OFFSET)."""
    del viewer_profile, connected_author_ids  # visibility/matching evaluated in SQL
    result = await db.execute(
        text(_FEED_COUNT_SQL),
        {"current_user": current_user_id},
    )
    return int(result.scalar_one())

# Fetch the post
async def fetch_feed_posts(
    db: AsyncSession,
    current_user_id: UUID,
    viewer_profile: Profile | None,
    connected_author_ids: set[UUID],
    *,
    cursor: str | None = None,
    limit: int | None = None,
) -> tuple[list[dict], str | None]:
    """
    Fetch feed events with keyset (cursor) pagination via a PostgreSQL CTE.

    Matching (university / major / minor, or an active connection):
    - Original post events match against the original author.
    - Repost events match against the reposter or the original author.
    - If any visible event matches, academic matches and connected users
      are returned together, ranked by engagement.
    - If nothing matches, all visible events are returned.

    Returns:
        (feed_items, next_cursor) where next_cursor is None on the last/empty page.
    """
    del viewer_profile, connected_author_ids  # visibility/matching evaluated in SQL
    from apps.feed.services.feed_cursor import decode_cursor, encode_cursor

    total_started = time.perf_counter()

    last_engagement_score: int = 0
    last_created_at: datetime = datetime.min
    last_post_id = UUID(int=0)
    has_cursor = False

    if cursor:
        decoded = decode_cursor(cursor)
        last_engagement_score = decoded["engagement_score"]
        last_created_at = decoded["created_at"]
        last_post_id = decoded["id"]
        has_cursor = True

    # Fetch one extra row to detect whether another page exists.
    fetch_limit = None if limit is None else limit + 1
    sql = _FEED_EVENTS_SQL if fetch_limit is None else _FEED_EVENTS_SQL + "\nLIMIT :limit"

    params: dict = {
        "current_user": current_user_id,
        "has_cursor": has_cursor,
        "last_engagement_score": last_engagement_score,
        "last_created_at": last_created_at,
        "last_post_id": last_post_id,
    }
    if fetch_limit is not None:
        params["limit"] = fetch_limit

    events = (await db.execute(text(sql), params)).mappings().all()
    if not events:
        logger.info(
            "[FEED_PERF] fetch_feed_posts_total=%.2fms events=0 hydrated_posts=0",
            _perf_ms(total_started),
        )
        return [], None

    has_more = False
    if limit is not None and len(events) > limit:
        has_more = True
        events = events[:limit]

    post_ids = {row["post_id"] for row in events}
    repost_ids = {row["repost_id"] for row in events if row["repost_id"]}

    # Phase 7: posts + reposts hydrated in one SQL round trip.
    hydration = await hydrate_feed_posts_and_reposts_raw(db, post_ids, repost_ids)
    posts_map = hydration.posts
    reposts_map = hydration.reposts

    # Rebuild in feed-event order; do not use hydration SQL row order.
    results: list[dict] = []
    for row in events:
        post_data = posts_map.get(row["post_id"])
        if not post_data:
            continue
        post, author_profile = post_data

        if row["event_type"] == "repost" and row["repost_id"]:
            repost_data = reposts_map.get(row["repost_id"])
            if not repost_data:
                continue
            repost, reposter_profile = repost_data
            results.append(
                {
                    "post": post,
                    "author_profile": author_profile,
                    "is_reposted": True,
                    "repost_id": repost.id,
                    "reposted_by_profile": reposter_profile,
                    "reposted_at": repost.created_at,
                    "_cursor_engagement_score": row["engagement_score"],
                    "_cursor_created_at": row["created_at"],
                    "_cursor_post_id": row["post_id"],
                }
            )
        else:
            results.append(
                {
                    "post": post,
                    "author_profile": author_profile,
                    "is_reposted": False,
                    "repost_id": None,
                    "reposted_by_profile": None,
                    "reposted_at": None,
                    "_cursor_engagement_score": row["engagement_score"],
                    "_cursor_created_at": row["created_at"],
                    "_cursor_post_id": row["post_id"],
                }
            )

    next_cursor = None
    if has_more and results:
        last = results[-1]
        next_cursor = encode_cursor(
            engagement_score=last["_cursor_engagement_score"],
            created_at=last["_cursor_created_at"],
            post_id=last["_cursor_post_id"],
        )

    for item in results:
        item.pop("_cursor_engagement_score", None)
        item.pop("_cursor_created_at", None)
        item.pop("_cursor_post_id", None)

    logger.info(
        "[FEED_PERF] fetch_feed_posts_total=%.2fms events=%s hydrated_posts=%s "
        "results=%s reposts=%s",
        _perf_ms(total_started),
        len(events),
        len(posts_map),
        len(results),
        len(reposts_map),
    )
    return results, next_cursor
