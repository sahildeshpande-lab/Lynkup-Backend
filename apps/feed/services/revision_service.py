from __future__ import annotations
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from sqlalchemy.orm import aliased
from apps.accounts.db_models import User
from apps.feed.db_models import Post, MediaAsset, PostAttachment, Hashtag, PostHashtag, PostRevision
from apps.feed.content_utils import sanitize_html, extract_hashtags
from apps.profiles.db_models import Profile
from common.enums import PostState
from common.exceptions import ApiError

# Author edit that moves a completed-review post back into the moderation queue.
_REVIEW_COMPLETED_STATES = frozenset(
    {
        PostState.flagged,
        PostState.rejected,
        PostState.reinstate,
        PostState.published,
    }
)

_CONTENT_DIFF_FIELDS = ("caption", "content_html", "visibility")


def _build_content_dict(payload_content) -> dict:
    """
    Build the content JSONB dict from a PostContentPayload,
    sanitizing HTML before storage.
    """
    content_dict: dict = {
        "caption": payload_content.caption,
        "visibility": payload_content.visibility,
    }
    if payload_content.content_html is not None:
        content_dict["content_html"] = sanitize_html(payload_content.content_html)
    return content_dict

async def _build_media_snapshot(
    post_id: UUID,
    db: AsyncSession,
) -> list[dict]:
    """
    Build a JSON-serialisable snapshot of the post's current media attachments
    for the revision audit trail.
    """
    stmt = (
        select(PostAttachment, MediaAsset)
        .join(MediaAsset, PostAttachment.media_asset_id == MediaAsset.id)
        .where(PostAttachment.post_id == post_id)
    )

    result = await db.execute(stmt)

    snapshot = []
    for attachment, asset in result.all():
        snapshot.append({
            "id": str(asset.id),
            "key": asset.key,
            "type": asset.type.value if hasattr(asset.type, "value") else str(asset.type),
            "original_filename": asset.original_filename,
            "mime_type": asset.mime_type,
            "file_size": asset.file_size,
        })

    return snapshot


def _should_trigger_moderation_review(
    previous_state: PostState | None,
    current_state: PostState,
) -> bool:
    """True only for the first edit that reopens moderation (→ processing)."""
    if previous_state is None:
        return False
    return (
        previous_state in _REVIEW_COMPLETED_STATES
        and current_state == PostState.processing
    )


async def _create_revision(
    post: Post,
    editor_user_id: UUID,
    db: AsyncSession,
    *,
    previous_state: PostState | None = None,
) -> None:
    """
    Create an immutable PostRevision audit record capturing the current
    state of the post's content and media.

    Sets ``triggered_moderation_review`` when this snapshot is the first
    author edit that moved a reviewed post back into ``processing``.
    """
    media_snapshot = await _build_media_snapshot(post.id, db)

    revision = PostRevision(
        post_id=post.id,
        editor_user_id=editor_user_id,
        content=post.content,
        media=media_snapshot,
        triggered_moderation_review=_should_trigger_moderation_review(
            previous_state,
            post.state,
        ),
    )

    db.add(revision)


def _resolve_editor_name(profile=None, user=None) -> str | None:
    if profile is not None:
        parts = [
            part
            for part in (getattr(profile, "first_name", None), getattr(profile, "last_name", None))
            if part
        ]
        name = " ".join(parts).strip()
        if name:
            return name
    if user is not None:
        email = getattr(user, "email", None)
        if email:
            return email
    return None


def _media_identity(media: list[dict] | None) -> list[str]:
    if not media:
        return []
    ids: list[str] = []
    for item in media:
        if not isinstance(item, dict):
            continue
        mid = item.get("id") or item.get("key")
        if mid is not None:
            ids.append(str(mid))
    return ids


def _content_changes(
    current: dict | None,
    previous: dict | None,
) -> dict:
    current = current or {}
    previous = previous or {}
    changes: dict = {}
    for field in _CONTENT_DIFF_FIELDS:
        old = previous.get(field)
        new = current.get(field)
        if old != new:
            changes[field] = {"from": old, "to": new}
    return changes


async def list_post_revisions_service(
    db: AsyncSession,
    post_id: UUID,
) -> list[dict]:
    """
    Return every content revision for a post (newest first).

    ``triggered_moderation_review`` is internal-only and is not included.
    """
    post = (
        await db.execute(select(Post).where(Post.id == post_id))
    ).scalar_one_or_none()
    if post is None:
        raise ApiError("Post not found")

    EditorProfile = aliased(Profile)
    stmt = (
        select(PostRevision, User, EditorProfile)
        .outerjoin(User, User.id == PostRevision.editor_user_id)
        .outerjoin(EditorProfile, EditorProfile.user_id == PostRevision.editor_user_id)
        .where(PostRevision.post_id == post_id)
        .order_by(PostRevision.created_at.desc(), PostRevision.id.desc())
    )
    rows = list((await db.execute(stmt)).all())

    items: list[dict] = []
    for index, (revision, editor_user, editor_profile) in enumerate(rows):
        older = rows[index + 1][0] if index + 1 < len(rows) else None
        previous_content = older.content if older is not None else None
        previous_media = older.media if older is not None else None
        items.append(
            {
                "id": revision.id,
                "post_id": revision.post_id,
                "editor_user_id": revision.editor_user_id,
                "editor_name": _resolve_editor_name(editor_profile, editor_user),
                "content": revision.content,
                "media": revision.media or [],
                "changes": _content_changes(revision.content, previous_content),
                "media_changed": _media_identity(revision.media)
                != _media_identity(previous_media),
                "created_at": revision.created_at,
            }
        )
    return items


async def _sync_hashtags(
    post_id: UUID,
    content: dict | None,
    db: AsyncSession,
) -> None:
    """
    Extract hashtags from caption and content_html and synchronise the
    ``PostHashtag`` join table.

    - New hashtags are inserted into the ``hashtags`` table (get-or-create).
    - All existing ``PostHashtag`` rows for this post are replaced.
    """
    # Delete existing mappings
    await db.execute(
        text("DELETE FROM post_hashtags WHERE post_id = :post_id").bindparams(post_id=post_id)
    )

    if not content:
        return

    tags = extract_hashtags(
        caption=content.get("caption"),
        content_html=content.get("content_html"),
    )
    if not tags:
        return

    for tag_text in tags:
        # Get or create the Hashtag record
        result = await db.execute(select(Hashtag).where(Hashtag.tag == tag_text))
        hashtag = result.scalar_one_or_none()
        if not hashtag:
            hashtag = Hashtag(tag=tag_text)
            db.add(hashtag)
            await db.flush()

        post_hashtag = PostHashtag(
            post_id=post_id,
            hashtag_id=hashtag.id,
        )
        db.add(post_hashtag)
