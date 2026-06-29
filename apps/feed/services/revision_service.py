from __future__ import annotations
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from apps.feed.db_models import Post, MediaAsset, PostAttachment, Hashtag, PostHashtag, PostRevision
from apps.feed.content_utils import sanitize_html, extract_hashtags

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

async def _create_revision(
    post: Post,
    editor_user_id: UUID,
    db: AsyncSession,
) -> None:
    """
    Create an immutable PostRevision audit record capturing the current
    state of the post's content and media.
    """
    media_snapshot = await _build_media_snapshot(post.id, db)

    revision = PostRevision(
        post_id=post.id,
        editor_user_id=editor_user_id,
        content=post.content,
        media=media_snapshot,
    )

    db.add(revision)

async def _sync_hashtags(
    post_id: UUID,
    content_html: str | None,
    db: AsyncSession,
) -> None:
    """
    Extract hashtags from ``content_html`` and synchronise the
    ``PostHashtag`` join table.

    - New hashtags are inserted into the ``hashtags`` table (get-or-create).
    - All existing ``PostHashtag`` rows for this post are replaced.
    """
    # Delete existing mappings
    await db.execute(
        text("DELETE FROM post_hashtags WHERE post_id = :post_id").bindparams(post_id=post_id)
    )

    if not content_html:
        return

    tags = extract_hashtags(content_html)
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
