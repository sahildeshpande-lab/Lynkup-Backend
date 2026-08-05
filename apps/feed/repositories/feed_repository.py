from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from apps.connections.db_models import Block
from apps.engagement.db_models import Repost
from apps.feed.db_models import Post, PostAttachment
from apps.feed.services.feed_cursor import decode_cursor, encode_cursor
from apps.profiles.db_models import Profile

#  to check post visibility
def _visible_author_sql(alias: str) -> str:
    """SQL fragment excluding soft-deleted / suspended / banned / deleting users."""
    return f"""
        {alias}.is_deleted = false
        AND {alias}.deleted_at IS NULL
        AND {alias}.status::text NOT IN ('suspended', 'banned', 'deleting')
    """

#  to check account status
def _not_blocked_sql(author_user_expr: str) -> str:
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
def _connected_sql(author_user_expr: str) -> str:
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

# Scoring for similary university , major , minor
def _relevance_sql(author_profile_alias: str) -> str:
    """Compute relevance once: university=4, major=2, minor=1."""
    return f"""
        (
            CASE
                WHEN me.university_id IS NOT NULL
                 AND {author_profile_alias}.university_id = me.university_id
                THEN 4
                ELSE 0
            END
            +
            CASE
                WHEN me.major IS NOT NULL
                 AND btrim(me.major) <> ''
                 AND {author_profile_alias}.major IS NOT NULL
                 AND lower(btrim({author_profile_alias}.major)) = lower(btrim(me.major))
                THEN 2
                ELSE 0
            END
            +
            CASE
                WHEN me.minor IS NOT NULL
                 AND btrim(me.minor) <> ''
                 AND {author_profile_alias}.minor IS NOT NULL
                 AND lower(btrim({author_profile_alias}.minor)) = lower(btrim(me.minor))
                THEN 1
                ELSE 0
            END
        )
    """
# Ranking post
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


_FEED_EVENTS_SQL = f"""
WITH ranked_posts AS (
    SELECT
        p.id AS post_id,
        CAST(NULL AS uuid) AS repost_id,
        p.created_at AS created_at,
        'post' AS event_type,
        {_relevance_sql("author_profile")} AS relevance
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
        {_relevance_sql("author_profile")} AS relevance
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
      AND {_visible_author_sql("reposter_user")}
      AND {_visible_author_sql("author_user")}
      AND {_visibility_sql("reposter_profile", "reposter_profile.user_id")}
      AND {_visibility_sql("author_profile", "p.author_user_id")}
      AND {_not_blocked_sql("reposter_profile.user_id")}
      AND {_not_blocked_sql("p.author_user_id")}
)
SELECT
    post_id,
    repost_id,
    created_at,
    event_type,
    relevance
FROM ranked_posts
WHERE
    (
        CAST(:has_cursor AS boolean) = false
        OR relevance < :last_relevance
        OR (
            relevance = :last_relevance
            AND created_at < :last_created_at
        )
        OR (
            relevance = :last_relevance
            AND created_at = :last_created_at
            AND post_id < :last_post_id
        )
    )
ORDER BY
    relevance DESC,
    created_at DESC,
    post_id DESC
"""

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
        p.id AS post_id
    FROM posts p
    JOIN profiles author_profile
        ON author_profile.user_id = p.author_user_id
    JOIN users author_user
        ON author_user.id = p.author_user_id
    WHERE p.state::text IN ('published', 'reinstate')
      AND p.author_user_id <> :current_user
      AND {_visible_author_sql("author_user")}
      AND {_visibility_sql("author_profile", "p.author_user_id")}
      AND {_not_blocked_sql("p.author_user_id")}

    UNION ALL

    SELECT
        p.id AS post_id
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
    WHERE p.state::text IN ('published', 'reinstate')
      AND reposter_profile.user_id <> :current_user
      AND {_visible_author_sql("reposter_user")}
      AND {_visible_author_sql("author_user")}
      AND {_visibility_sql("reposter_profile", "reposter_profile.user_id")}
      AND {_visibility_sql("author_profile", "p.author_user_id")}
      AND {_not_blocked_sql("reposter_profile.user_id")}
      AND {_not_blocked_sql("p.author_user_id")}
)
SELECT COUNT(*) FROM ranked_posts
"""

# count the post
async def count_feed_posts(
    db: AsyncSession,
    current_user_id: UUID,
    viewer_profile: Profile | None,
    connected_author_ids: set[UUID],
) -> int:
    """Count feed events using the same ranked CTE visibility rules (no OFFSET)."""
    del viewer_profile, connected_author_ids  # visibility is evaluated in SQL
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

    Returns:
        (feed_items, next_cursor) where next_cursor is None on the last/empty page.
    """
    del viewer_profile, connected_author_ids  # visibility/relevance evaluated in SQL

    last_relevance = 0
    last_created_at: datetime = datetime.min
    last_post_id = UUID(int=0)
    has_cursor = False

    if cursor:
        decoded = decode_cursor(cursor)
        last_relevance = decoded["relevance"]
        last_created_at = decoded["created_at"]
        last_post_id = decoded["id"]
        has_cursor = True

    # Fetch one extra row to detect whether another page exists.
    fetch_limit = None if limit is None else limit + 1
    sql = _FEED_EVENTS_SQL if fetch_limit is None else _FEED_EVENTS_SQL + "\nLIMIT :limit"

    params: dict = {
        "current_user": current_user_id,
        "has_cursor": has_cursor,
        "last_relevance": last_relevance,
        "last_created_at": last_created_at,
        "last_post_id": last_post_id,
    }
    if fetch_limit is not None:
        params["limit"] = fetch_limit

    events = (await db.execute(text(sql), params)).mappings().all()
    if not events:
        return [], None

    has_more = False
    if limit is not None and len(events) > limit:
        has_more = True
        events = events[:limit]

    post_ids = {row["post_id"] for row in events}
    repost_ids = {row["repost_id"] for row in events if row["repost_id"]}

    post_author_profile = aliased(Profile, name="post_author_profile")
    post_stmt = (
        select(Post, post_author_profile)
        .join(post_author_profile, post_author_profile.user_id == Post.author_user_id)
        .where(Post.id.in_(post_ids))
        .options(selectinload(Post.attachments).selectinload(PostAttachment.media_asset))
    )
    post_results = await db.execute(post_stmt)
    posts_map = {post.id: (post, profile) for post, profile in post_results.all()}

    reposts_map: dict = {}
    if repost_ids:
        repost_stmt = (
            select(Repost, Profile)
            .join(Profile, Profile.id == Repost.profile_id)
            .where(Repost.id.in_(repost_ids))
        )
        repost_results = await db.execute(repost_stmt)
        reposts_map = {repost.id: (repost, profile) for repost, profile in repost_results.all()}

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
                    "_relevance": int(row["relevance"]),
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
                    "_relevance": int(row["relevance"]),
                    "_cursor_created_at": row["created_at"],
                    "_cursor_post_id": row["post_id"],
                }
            )

    next_cursor = None
    if has_more and results:
        last = results[-1]
        next_cursor = encode_cursor(
            relevance=last["_relevance"],
            created_at=last["_cursor_created_at"],
            post_id=last["_cursor_post_id"],
        )

    for item in results:
        item.pop("_relevance", None)
        item.pop("_cursor_created_at", None)
        item.pop("_cursor_post_id", None)

    return results, next_cursor
