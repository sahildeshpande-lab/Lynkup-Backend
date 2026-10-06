"""Raw-SQL post hydration for /feed (Phase 1).

Loads only columns required by ``format_post_detail`` / ``_extract_post_media``.
Does not instantiate Post/Profile/PostAttachment/MediaAsset ORM entities.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Attachment order matches prior selectinload behavior (related PK ascending).
_POST_HYDRATION_SQL = """
SELECT
    p.id AS post_id,
    p.author_user_id AS post_author_user_id,
    p.state AS post_state,
    p.revision_number AS post_revision_number,
    p.content AS post_content,
    p.created_at AS post_created_at,
    p.updated_at AS post_updated_at,
    p.is_edited AS post_is_edited,
    p.like_count AS post_like_count,
    p.repost_count AS post_repost_count,
    p.share_count AS post_share_count,
    p.comment_count AS post_comment_count,
    p.is_moderator_reviewed AS post_is_moderator_reviewed,
    p.reviewed_at AS post_reviewed_at,
    p.moderator_id AS post_moderator_id,
    p.moderation_notes AS post_moderation_notes,
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
FROM posts p
JOIN profiles pr ON pr.user_id = p.author_user_id
LEFT JOIN post_attachments pa ON pa.post_id = p.id
LEFT JOIN media_assets ma ON ma.id = pa.media_asset_id
WHERE p.id IN :post_ids
ORDER BY pa.id ASC NULLS LAST
"""


@dataclass
class FeedMediaAssetRow:
    id: UUID
    key: str
    type: Any
    original_filename: str | None
    mime_type: str | None
    file_size: int | None


@dataclass
class FeedAttachmentRow:
    id: UUID
    media_asset: FeedMediaAssetRow | None


@dataclass
class FeedPostRow:
    """Lightweight post shape compatible with ``format_post_detail``."""

    id: UUID
    author_user_id: UUID
    state: Any
    revision_number: int
    content: dict | None
    created_at: Any
    updated_at: Any
    is_edited: bool
    like_count: int
    repost_count: int
    share_count: int
    comment_count: int
    is_moderator_reviewed: bool
    reviewed_at: Any
    moderator_id: UUID | None
    moderation_notes: str | None
    attachments: list[FeedAttachmentRow] = field(default_factory=list)


@dataclass
class FeedProfileRow:
    """Lightweight author profile shape for feed formatting / enrichment."""

    user_id: UUID
    first_name: str | None
    last_name: str | None
    profile_photo_url: str | None
    profile_visibility: Any
    university_id: UUID | None
    profile_interests_id: list | None
    bio: str | None
    major: str | None
    minor: str | None
    edu_level: str | None


def _perf_ms(started_at: float) -> float:
    return (time.perf_counter() - started_at) * 1000.0


def _group_hydration_rows(
    rows: list[Any],
) -> dict[UUID, tuple[FeedPostRow, FeedProfileRow]]:
    """Collapse join rows into one post+profile per post_id; media ordered by attachment id."""
    posts: dict[UUID, FeedPostRow] = {}
    profiles: dict[UUID, FeedProfileRow] = {}
    seen_attachments: dict[UUID, set[UUID]] = {}

    for row in rows:
        post_id = row["post_id"]
        if post_id not in posts:
            posts[post_id] = FeedPostRow(
                id=post_id,
                author_user_id=row["post_author_user_id"],
                state=row["post_state"],
                revision_number=row["post_revision_number"],
                content=row["post_content"] or {},
                created_at=row["post_created_at"],
                updated_at=row["post_updated_at"],
                is_edited=bool(row["post_is_edited"]),
                like_count=int(row["post_like_count"] or 0),
                repost_count=int(row["post_repost_count"] or 0),
                share_count=int(row["post_share_count"] or 0),
                comment_count=int(row["post_comment_count"] or 0),
                is_moderator_reviewed=bool(row["post_is_moderator_reviewed"]),
                reviewed_at=row["post_reviewed_at"],
                moderator_id=row["post_moderator_id"],
                moderation_notes=row["post_moderation_notes"],
                attachments=[],
            )
            profiles[post_id] = FeedProfileRow(
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
            )
            seen_attachments[post_id] = set()

        attachment_id = row["attachment_id"]
        if attachment_id is None:
            continue
        if attachment_id in seen_attachments[post_id]:
            continue
        seen_attachments[post_id].add(attachment_id)

        media: FeedMediaAssetRow | None = None
        if row["media_id"] is not None:
            media = FeedMediaAssetRow(
                id=row["media_id"],
                key=row["media_key"],
                type=row["media_type"],
                original_filename=row["media_original_filename"],
                mime_type=row["media_mime_type"],
                file_size=row["media_file_size"],
            )
        posts[post_id].attachments.append(
            FeedAttachmentRow(id=attachment_id, media_asset=media)
        )

    # Match prior selectinload collection order: related PK ascending.
    for post in posts.values():
        post.attachments.sort(key=lambda attachment: attachment.id)

    return {post_id: (posts[post_id], profiles[post_id]) for post_id in posts}


async def hydrate_feed_posts_raw(
    db: AsyncSession,
    post_ids: set[UUID] | list[UUID],
) -> dict[UUID, tuple[FeedPostRow, FeedProfileRow]]:
    """Batch-hydrate posts + author profiles + media via one raw SQL statement.

    Returns a map keyed by post id. Callers must re-order using the feed event
    sequence — this map does not preserve feed order.
    """
    unique_ids = list({pid for pid in post_ids if pid is not None})
    if not unique_ids:
        logger.info(
            "[FEED_PERF] raw_post_hydration_total=0.00ms requested_posts=0 "
            "rows=0 hydrated_posts=0 attachments=0"
        )
        return {}

    stmt = text(_POST_HYDRATION_SQL).bindparams(
        bindparam("post_ids", expanding=True)
    )

    total_started = time.perf_counter()
    execute_started = time.perf_counter()
    result = await db.execute(stmt, {"post_ids": unique_ids})
    execute_ms = _perf_ms(execute_started)

    fetch_started = time.perf_counter()
    rows = result.mappings().all()
    fetch_ms = _perf_ms(fetch_started)

    map_started = time.perf_counter()
    grouped = _group_hydration_rows(rows)
    map_ms = _perf_ms(map_started)

    attachment_count = sum(len(post.attachments) for post, _profile in grouped.values())
    total_ms = _perf_ms(total_started)

    logger.info(
        "[FEED_PERF] raw_post_hydration_execute=%.2fms requested_posts=%s",
        execute_ms,
        len(unique_ids),
    )
    logger.info(
        "[FEED_PERF] raw_post_hydration_fetch=%.2fms rows=%s",
        fetch_ms,
        len(rows),
    )
    logger.info(
        "[FEED_PERF] raw_post_hydration_mapping=%.2fms hydrated_posts=%s attachments=%s",
        map_ms,
        len(grouped),
        attachment_count,
    )
    logger.info(
        "[FEED_PERF] raw_post_hydration_total=%.2fms requested_posts=%s rows=%s "
        "hydrated_posts=%s attachments=%s sql_statements=1",
        total_ms,
        len(unique_ids),
        len(rows),
        len(grouped),
        attachment_count,
    )
    return grouped
