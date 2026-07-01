from __future__ import annotations
from typing import Literal
from datetime import datetime, timezone
from uuid import UUID
from common.exceptions import ApiError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from common.enums import PostState
from apps.accounts.db_models import User
from apps.feed.db_models import Post
from apps.feed.schemas import SavePostRequest, EditPostRequest
from apps.feed.content_utils import sanitize_html, validate_content, validate_media_count
from core.images import generate_download_url
from apps.connections.services.connection_service import is_blocked

from .media_service import _verify_and_attach_media
from .revision_service import _build_content_dict, _create_revision, _sync_hashtags
from apps.moderation.services.moderator_assignment_service import assign_next_moderator_round_robin

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def _extract_post_media(post: Post) -> list[dict]:
    media_data = []
    if post.attachments:
        for attachment in post.attachments:
            asset = attachment.media_asset
            if asset:
                media_data.append({
                    "id": asset.id,
                    "key": asset.key,
                    "type": asset.type,
                    "url": generate_download_url(asset.key),
                    "original_filename": asset.original_filename,
                    "mime_type": asset.mime_type,
                    "file_size": asset.file_size,
                })
    return media_data

def format_post_detail(post: Post) -> dict:
    """
    Format a Post model and its attachments into a dictionary matching PostDetailData schema.
    """
    media_data = _extract_post_media(post)

    content = post.content or {}
    return {
        "id": post.id,
        "author_user_id": post.author_user_id,
        "state": post.state.value if hasattr(post.state, "value") else str(post.state),
        "revision_number": post.revision_number,
        "content": {
            "caption": content.get("caption"),
            "content_html": content.get("content_html"),
            "visibility": content.get("visibility", "public"),
        },
        "created_at": post.created_at,
        "updated_at": post.updated_at,
        "media": media_data
    }

async def _assign_moderator_if_processing(
    post: Post,
    db: AsyncSession,
    *,
    previous_state: PostState | None = None,
) -> None:
    if post.state != PostState.processing:
        return
    if previous_state == PostState.processing and post.moderator_id is not None:
        return
    post.moderator_id = await assign_next_moderator_round_robin(db)

async def save_post_service(
    user_id: UUID,
    payload: SavePostRequest,
    db: AsyncSession
) -> Post:
    """
    Unified create-or-update draft service.

    - If ``payload.id`` is ``None``: create a new draft (revision_number = 1).
    - If ``payload.id`` is provided: update the existing draft (revision_number += 1).

    In both paths the service validates content, sanitises HTML, manages
    attachment mappings, synchronises hashtags, and writes an immutable
    PostRevision audit snapshot.
    """
    # Validate media count
    try:
        validate_media_count(payload.media)
    except ValueError as exc:
        raise ApiError(str(exc))

    # Build content dict (includes HTML sanitization)
    content_dict = _build_content_dict(payload.content)

    # Validate content
    has_media = bool(payload.media)
    try:
        validate_content(content_dict, has_media)
    except ValueError as exc:
        raise ApiError(str(exc))

    # Determine state
    if payload.id is not None and payload.is_draft:
        post_state = PostState.hidden if payload.content.visibility == "hidden" else PostState.draft
    else:
        post_state = PostState.processing

    # ----- CREATE -----
    if payload.id is None:
        post = Post(
            author_user_id=user_id,
            content=content_dict,
            state=post_state,
            revision_number=1,
        )
        db.add(post)

        try:
            await db.flush()

            if payload.media:
                await _verify_and_attach_media(
                    post_id=post.id,
                    user_id=user_id,
                    media_items=payload.media,
                    db=db,
                    replace=False,
                )

            await _sync_hashtags(post.id, content_dict, db)
            await _create_revision(post, user_id, db)
            await _assign_moderator_if_processing(post, db)

            await db.commit()
            await db.refresh(post)
        except ApiError:
            await db.rollback()
            raise
        except Exception as e:
            await db.rollback()
            raise ApiError("Failed to create post")

        return post

    # ----- UPDATE -----
    result = await db.execute(select(Post).where(Post.id == payload.id))
    post = result.scalar_one_or_none()

    if not post:
        raise ApiError("Post not found")
    if post.author_user_id != user_id:
        raise ApiError("Post does not belong to the authenticated user")

    # Only drafts, hidden, and processing posts are editable through this endpoint
    if post.state not in (PostState.draft, PostState.hidden, PostState.processing):
        raise ApiError(f"Post is in '{post.state.value}' state and cannot be edited")

    post.content = content_dict
    previous_state = post.state
    post.state = post_state
    post.revision_number += 1
    post.updated_at = utc_now()

    try:
        if payload.media is not None:
            await _verify_and_attach_media(
                post_id=post.id,
                user_id=user_id,
                media_items=payload.media,
                db=db,
                replace=True,
            )

        await _sync_hashtags(post.id, content_dict, db)
        await _create_revision(post, user_id, db)
        await _assign_moderator_if_processing(
            post, db, previous_state=previous_state
        )

        await db.commit()
        await db.refresh(post)
    except ApiError:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise ApiError("Failed to update post")

    return post

async def edit_post_service(
    user_id: UUID,
    payload: EditPostRequest,
    db: AsyncSession
) -> Post:
    """
    Edit/update fields of an existing post. All fields (and content fields) are optional.
    """
    result = await db.execute(select(Post).where(Post.id == payload.id))
    post = result.scalar_one_or_none()

    if not post:
        raise ApiError("Post not found")
    if post.author_user_id != user_id:
        raise ApiError("Post does not belong to the authenticated user")

    # Validate media count if media payload is provided
    if payload.media is not None:
        try:
            validate_media_count(payload.media)
        except ValueError as exc:
            raise ApiError(str(exc))

    # Build updated/merged content dict
    merged_content = dict(post.content or {})
    if payload.content is not None:
        if payload.content.caption is not None:
            merged_content["caption"] = payload.content.caption
        if payload.content.content_html is not None:
            merged_content["content_html"] = sanitize_html(payload.content.content_html)
        if payload.content.visibility is not None:
            merged_content["visibility"] = payload.content.visibility

    # Validate final merged content against final media state
    if payload.media is not None:
        has_media = len(payload.media) > 0
    else:
        has_media = len(post.attachments) > 0

    try:
        validate_content(merged_content, has_media)
    except ValueError as exc:
        raise ApiError(str(exc))

    # Apply changes to model
    post.content = merged_content

    # Determine state from visibility if provided
    if payload.content is not None and payload.content.visibility is not None:
        if payload.content.visibility == "hidden":
            post.state = PostState.hidden
        elif payload.content.visibility == "public" and post.state == PostState.hidden:
            post.state = PostState.draft

    post.revision_number += 1
    post.updated_at = utc_now()

    try:
        # Manage media attachments if provided
        if payload.media is not None:
            await _verify_and_attach_media(
                post_id=post.id,
                user_id=user_id,
                media_items=payload.media,
                db=db,
                replace=True,
            )

        # Re-sync hashtags from current caption and content_html
        await _sync_hashtags(post.id, merged_content, db)

        # Create revision audit record
        await _create_revision(post, user_id, db)

        await db.commit()
        await db.refresh(post)
    except ApiError:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise ApiError("Failed to edit post")

    return post

async def publish_post_service(
    post_id: UUID,
    user_id: UUID,
    db: AsyncSession
) -> Post:
    """
    Publish a post.

    State transitions:
    - draft   → published
    - hidden  → hidden  (stays hidden)

    If visibility inside content is ``"hidden"`` the post stays ``PostState.hidden``.
    """
    result = await db.execute(select(Post).where(Post.id == post_id))
    post = result.scalar_one_or_none()

    if not post:
        raise ApiError("Post not found")
    if post.author_user_id != user_id:
        raise ApiError("Post does not belong to the authenticated user")

    # Only draft, hidden, or processing posts can be published
    if post.state not in (PostState.draft, PostState.hidden, PostState.processing):
        raise ApiError(f"Post is in '{post.state.value}' state and cannot be published")

    # State transition: respect visibility stored in content
    visibility = (post.content or {}).get("visibility", "public")
    if visibility == "hidden" or post.state == PostState.hidden:
        post.state = PostState.hidden
    else:
        post.state = PostState.published

    # Increment revision number and update timestamp
    post.revision_number += 1
    post.updated_at = utc_now()

    try:
        # Create revision audit record
        await _create_revision(post, user_id, db)

        await db.commit()
        await db.refresh(post)
    except Exception as e:
        await db.rollback()
        raise ApiError("Failed to publish post")

    return post


async def admin_publish_post_service(
    post_id: UUID,
    action: Literal["publish", "flag"],
    admin_user_id: UUID,
    db: AsyncSession
) -> Post:
    """
    Publish or flag a post by an admin.
    """
    result = await db.execute(select(Post).where(Post.id == post_id))
    post = result.scalar_one_or_none()

    if not post:
        raise ApiError("Post not found")

    if action == "publish":
        # Respect visibility stored in content
        visibility = (post.content or {}).get("visibility", "public")
        if visibility == "hidden" or post.state == PostState.hidden:
            post.state = PostState.hidden
        else:
            post.state = PostState.published
    elif action == "flag":
        post.state = PostState.flagged
    else:
        raise ApiError(f"Invalid action: {action}")

    post.is_moderator_reviewed = True
    post.reviewed_at = utc_now()

    # Increment revision number and update timestamp
    post.revision_number += 1
    post.updated_at = utc_now()

    try:
        # Create revision audit record with the admin user as the editor
        await _create_revision(post, admin_user_id, db)

        await db.commit()
        await db.refresh(post)
    except Exception as e:
        await db.rollback()
        raise ApiError("Failed to publish or flag post")

    return post

async def get_post_service(
    post_id: UUID,
    user_id: UUID,
    db: AsyncSession
) -> Post:
    """
    Retrieve details of a specific post.
    Validates visibility access permissions.
    """
    result = await db.execute(select(Post).where(Post.id == post_id))
    post = result.scalar_one_or_none()

    if not post:
        raise ApiError("Post not found")

    if post.state == PostState.deleted:
        if post.author_user_id != user_id:
            raise ApiError("Post not found")
        return post

    if post.state == PostState.flagged and post.author_user_id != user_id:
        raise ApiError("Post is not accessible")

    if post.state in (PostState.draft, PostState.hidden, PostState.processing):
        if post.author_user_id != user_id:
            raise ApiError("Post is not accessible")
        return post

    if post.author_user_id != user_id:
        if await is_blocked(db, user_id, post.author_user_id):
            raise ApiError("Post is not accessible")

        visibility = (post.content or {}).get("visibility", "public")
        if visibility == "hidden":
            raise ApiError("Post is not accessible")
        if visibility == "connections_only":
            from apps.connections.services.connection_service import are_connected
            if not await are_connected(db, user_id, post.author_user_id):
                raise ApiError("Post is not accessible")

    return post

async def delete_post_service(
    post_id: UUID,
    user_id: UUID,
    db: AsyncSession
) -> Post:
    """
    Soft-delete a post by marking its state as deleted.
    """
    result = await db.execute(select(Post).where(Post.id == post_id))
    post = result.scalar_one_or_none()

    if not post:
        raise ApiError("Post not found")
    if post.author_user_id != user_id:
        raise ApiError("Post does not belong to the authenticated user")

    post.state = PostState.deleted
    post.updated_at = utc_now()

    try:
        await db.commit()
        await db.refresh(post)
    except Exception as e:
        await db.rollback()
        raise ApiError("Failed to delete post")

    return post

_OWNER_POST_STATES = (
    PostState.draft,
    PostState.processing,
    PostState.published,
    PostState.flagged,
    PostState.hidden,
    PostState.deleted,
)

async def list_user_posts_service(
    target_user_id: UUID,
    current_user_id: UUID,
    db: AsyncSession
) -> list[Post]:
    """
    List posts for a user.
    Owners see draft, processing, published, flagged, hidden, and deleted posts.
    Other users see only published posts.
    """
    # Verify user exists
    user_result = await db.execute(select(User).where(User.id == target_user_id))
    if not user_result.scalar_one_or_none():
        raise ApiError("User not found")

    stmt = select(Post).where(Post.author_user_id == target_user_id)
    if target_user_id == current_user_id:
        stmt = stmt.where(Post.state.in_(_OWNER_POST_STATES))
    else:
        stmt = stmt.where(Post.state == PostState.published)

    stmt = stmt.order_by(Post.created_at.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


def _format_processing_post_item(post: Post, profile) -> dict:
    content = post.content or {}
    return {
        "user_id": post.author_user_id,
        "first_name": profile.first_name if profile else None,
        "last_name": profile.last_name if profile else None,
        "profile_photo_url": (
            generate_download_url(profile.profile_photo_url)
            if profile and profile.profile_photo_url
            else None
        ),
        "post_id": post.id,
        "caption": content.get("caption"),
        "content_html": content.get("content_html"),
        "media": _extract_post_media(post),
        "is_moderator_reviewed": post.is_moderator_reviewed,
        "moderator_id": post.moderator_id,
        "reviewed_at": post.reviewed_at,
    }


async def list_processing_posts_service(
    db: AsyncSession,
    page: int | None = None,
    page_size: int | None = None,
) -> dict:
    from sqlalchemy import func
    from sqlalchemy.orm import selectinload
    from apps.feed.db_models import PostAttachment
    from apps.profiles.db_models import Profile
    from common.pagination import build_paginated_response

    base_filter = Post.state == PostState.processing
    total_items = int((await db.execute(select(func.count(Post.id)).where(base_filter))).scalar_one())

    stmt = (
        select(Post, Profile)
        .outerjoin(Profile, Profile.user_id == Post.author_user_id)
        .where(base_filter)
        .options(selectinload(Post.attachments).selectinload(PostAttachment.media_asset))
        .order_by(Post.created_at.desc())
    )
    if page is not None and page_size is not None:
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)

    rows = (await db.execute(stmt)).all()
    items = [_format_processing_post_item(post, profile) for post, profile in rows]

    p = page or 1
    ps = page_size if page_size is not None else (len(items) if items else 1)
    return build_paginated_response(items, p, ps, total_items).model_dump()
