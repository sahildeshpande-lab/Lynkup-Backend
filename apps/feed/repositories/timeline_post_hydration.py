"""Raw-SQL post hydration for GET /posts timeline (Phase 1).

Loads only columns required by ``format_post_detail`` / ``_extract_post_media``,
plus moderator name fields. Author profile uses LEFT JOIN so posts without a
profile row are still returned.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

# Attachment order matches prior selectinload behavior (related PK ascending).
_TIMELINE_POST_HYDRATION_SQL = """
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
    ap.user_id AS profile_user_id,
    ap.first_name AS profile_first_name,
    ap.last_name AS profile_last_name,
    ap.profile_photo_url AS profile_photo_url,
    ap.profile_visibility AS profile_visibility,
    ap.university_id AS profile_university_id,
    ap.profile_interests_id AS profile_interests_id,
    ap.bio AS profile_bio,
    ap.major AS profile_major,
    ap.minor AS profile_minor,
    ap.edu_level AS profile_edu_level,
    mu.email AS moderator_user_email,
    mp.first_name AS moderator_profile_first_name,
    mp.last_name AS moderator_profile_last_name,
    pa.id AS attachment_id,
    ma.id AS media_id,
    ma.key AS media_key,
    ma.type AS media_type,
    ma.original_filename AS media_original_filename,
    ma.mime_type AS media_mime_type,
    ma.file_size AS media_file_size
FROM posts p
LEFT JOIN profiles ap ON ap.user_id = p.author_user_id
LEFT JOIN users mu ON mu.id = p.moderator_id
LEFT JOIN profiles mp ON mp.user_id = p.moderator_id
LEFT JOIN post_attachments pa ON pa.post_id = p.id
LEFT JOIN media_assets ma ON ma.id = pa.media_asset_id
WHERE p.id IN :post_ids
ORDER BY pa.id ASC NULLS LAST
"""


@dataclass
class TimelineMediaAssetRow:
    id: UUID
    key: str
    type: Any
    original_filename: str | None
    mime_type: str | None
    file_size: int | None


@dataclass
class TimelineAttachmentRow:
    id: UUID
    media_asset: TimelineMediaAssetRow | None


@dataclass
class TimelinePostRow:
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
    attachments: list[TimelineAttachmentRow] = field(default_factory=list)


@dataclass
class TimelineProfileRow:
    """Lightweight author profile shape for formatting / profile enrichment."""

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


@dataclass
class TimelineModeratorUserRow:
    email: str


@dataclass
class TimelineModeratorProfileRow:
    first_name: str | None
    last_name: str | None


TimelineHydrationRow = tuple[
    TimelinePostRow,
    TimelineProfileRow | None,
    TimelineModeratorUserRow | None,
    TimelineModeratorProfileRow | None,
]


def _moderator_user_from_row(row: Any) -> TimelineModeratorUserRow | None:
    email = row.get("moderator_user_email")
    if email is None:
        return None
    return TimelineModeratorUserRow(email=email)


def _moderator_profile_from_row(row: Any) -> TimelineModeratorProfileRow | None:
    if row.get("moderator_profile_first_name") is None and row.get(
        "moderator_profile_last_name"
    ) is None:
        return None
    return TimelineModeratorProfileRow(
        first_name=row.get("moderator_profile_first_name"),
        last_name=row.get("moderator_profile_last_name"),
    )


def _author_profile_from_row(row: Any) -> TimelineProfileRow | None:
    if row.get("profile_user_id") is None:
        return None
    return TimelineProfileRow(
        user_id=row["profile_user_id"],
        first_name=row.get("profile_first_name"),
        last_name=row.get("profile_last_name"),
        profile_photo_url=row.get("profile_photo_url"),
        profile_visibility=row.get("profile_visibility"),
        university_id=row.get("profile_university_id"),
        profile_interests_id=row.get("profile_interests_id") or [],
        bio=row.get("profile_bio"),
        major=row.get("profile_major"),
        minor=row.get("profile_minor"),
        edu_level=row.get("profile_edu_level"),
    )


def group_timeline_hydration_rows(
    rows: list[Any],
) -> dict[UUID, TimelineHydrationRow]:
    """Collapse join rows into one hydrated post per post_id."""
    posts: dict[UUID, TimelinePostRow] = {}
    author_profiles: dict[UUID, TimelineProfileRow | None] = {}
    moderator_users: dict[UUID, TimelineModeratorUserRow | None] = {}
    moderator_profiles: dict[UUID, TimelineModeratorProfileRow | None] = {}
    seen_attachments: dict[UUID, set[UUID]] = {}

    for row in rows:
        post_id = row["post_id"]
        if post_id not in posts:
            posts[post_id] = TimelinePostRow(
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
            author_profiles[post_id] = _author_profile_from_row(row)
            moderator_users[post_id] = _moderator_user_from_row(row)
            moderator_profiles[post_id] = _moderator_profile_from_row(row)
            seen_attachments[post_id] = set()

        attachment_id = row["attachment_id"]
        if attachment_id is None:
            continue
        if attachment_id in seen_attachments[post_id]:
            continue
        seen_attachments[post_id].add(attachment_id)

        media: TimelineMediaAssetRow | None = None
        if row["media_id"] is not None:
            media = TimelineMediaAssetRow(
                id=row["media_id"],
                key=row["media_key"],
                type=row["media_type"],
                original_filename=row["media_original_filename"],
                mime_type=row["media_mime_type"],
                file_size=row["media_file_size"],
            )
        posts[post_id].attachments.append(
            TimelineAttachmentRow(id=attachment_id, media_asset=media)
        )

    for post in posts.values():
        post.attachments.sort(key=lambda attachment: attachment.id)

    return {
        post_id: (
            posts[post_id],
            author_profiles[post_id],
            moderator_users[post_id],
            moderator_profiles[post_id],
        )
        for post_id in posts
    }


async def hydrate_timeline_posts_raw(
    db: AsyncSession,
    post_ids: set[UUID] | list[UUID],
) -> dict[UUID, TimelineHydrationRow]:
    """Batch-hydrate timeline posts via one raw SQL statement."""
    unique_ids = list({pid for pid in post_ids if pid is not None})
    if not unique_ids:
        return {}

    stmt = text(_TIMELINE_POST_HYDRATION_SQL).bindparams(
        bindparam("post_ids", expanding=True)
    )
    result = await db.execute(stmt, {"post_ids": unique_ids})
    return group_timeline_hydration_rows(result.mappings().all())
