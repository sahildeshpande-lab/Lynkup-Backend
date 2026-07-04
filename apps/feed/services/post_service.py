from __future__ import annotations
import logging
from typing import Literal
from datetime import datetime, timezone
from uuid import UUID
from fastapi import HTTPException, status
from common.exceptions import ApiError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from common.enums import PostState
from apps.accounts.db_models import User
from apps.feed.db_models import Post
from apps.feed.schemas import SavePostRequest, EditPostRequest
from apps.feed.content_utils import sanitize_html, validate_content, validate_media_count
from core.images import generate_download_url, generate_profile_image_url
from apps.connections.services.connection_service import is_blocked

from .media_service import _verify_and_attach_media
from .revision_service import _build_content_dict, _create_revision, _sync_hashtags
from apps.moderation.services.moderator_assignment_service import assign_next_moderator_round_robin

logger = logging.getLogger(__name__)

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
    data = {
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
    author_profile = getattr(post, "_author_profile", None)
    if author_profile is not None:
        data.update({
            "first_name": author_profile.first_name,
            "last_name": author_profile.last_name,
            "profile_photo_url": (
                generate_profile_image_url(author_profile.profile_photo_url)
                if author_profile.profile_photo_url
                else None
            ),
        })
    return data

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


async def _soft_delete_other_drafts(
    db: AsyncSession,
    user_id: UUID,
    *,
    exclude_post_id: UUID | None = None,
) -> None:
    stmt = select(Post).where(
        Post.author_user_id == user_id,
        Post.state == PostState.draft,
    )
    if exclude_post_id is not None:
        stmt = stmt.where(Post.id != exclude_post_id)

    result = await db.execute(stmt)
    for draft in result.scalars().all():
        draft.state = PostState.deleted
        draft.updated_at = utc_now()

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
    try:
        validate_content(content_dict)
    except ValueError as exc:
        raise ApiError(str(exc))

    # Determine state
    post_state = PostState.draft if payload.is_draft else PostState.processing

    # ----- CREATE -----
    if payload.id is None:
        post = Post(
            author_user_id=user_id,
            content=content_dict,
            state=post_state,
            revision_number=1,
        )

        try:
            if post_state == PostState.draft:
                await _soft_delete_other_drafts(db, user_id)

            db.add(post)
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
        if post_state == PostState.draft:
            await _soft_delete_other_drafts(db, user_id, exclude_post_id=post.id)

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

    try:
        validate_content(merged_content)
    except ValueError as exc:
        raise ApiError(str(exc))

    # Apply changes to model
    post.content = merged_content

    # Determine state from visibility if provided
    if payload.content is not None and payload.content.visibility is not None:
        if payload.content.visibility in ("private", "hidden"):
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
    - private → hidden  (stays private)

    If visibility inside content is ``"private"`` the post stays ``PostState.hidden``.
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

    previous_state = post.state

    # State transition: respect visibility stored in content
    visibility = (post.content or {}).get("visibility", "public")
    if visibility in ("private", "hidden") or post.state == PostState.hidden:
        post.state = PostState.hidden
    else:
        post.state = PostState.published

    # Increment revision number and update timestamp
    post.revision_number += 1
    post.updated_at = utc_now()

    try:
        if previous_state != PostState.published and post.state == PostState.published:
            from apps.profiles.services.profile_stats_service import increment_posts_count_for_user

            await increment_posts_count_for_user(db, post.author_user_id)

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
    status: Literal["publish", "flag"],
    admin_user_id: UUID,
    db: AsyncSession
) -> Post:
    """
    Publish or flag a post by a moderator/admin.
    """
    from apps.profiles.db_models import Profile

    result = await db.execute(select(Post).where(Post.id == post_id))
    post = result.scalar_one_or_none()

    if not post:
        raise ApiError("Post not found")

    author_result = await db.execute(
        select(User, Profile)
        .outerjoin(Profile, Profile.user_id == User.id)
        .where(User.id == post.author_user_id)
    )
    author_row = author_result.first()
    author_user: User | None = None
    author_full_name: str | None = None
    if author_row:
        author_user, author_profile = author_row
        if author_profile:
            author_full_name = " ".join(
                part for part in (author_profile.first_name, author_profile.last_name) if part
            ).strip() or None

    previous_state = post.state

    if status == "publish":
        # Respect visibility stored in content
        visibility = (post.content or {}).get("visibility", "public")
        if visibility in ("private", "hidden") or post.state == PostState.hidden:
            post.state = PostState.hidden
        else:
            post.state = PostState.published
    elif status == "flag":
        post.state = PostState.flagged
    else:
        raise ApiError(f"Invalid status: {status}")

    post.moderator_id = admin_user_id
    post.is_moderator_reviewed = True
    post.reviewed_at = utc_now()

    # Increment revision number and update timestamp
    post.revision_number += 1
    post.updated_at = utc_now()

    try:
        if previous_state != PostState.published and post.state == PostState.published:
            from apps.profiles.services.profile_stats_service import increment_posts_count_for_user

            await increment_posts_count_for_user(db, post.author_user_id)

        # Create revision audit record with the admin user as the editor
        await _create_revision(post, admin_user_id, db)

        await db.commit()
        await db.refresh(post)
    except Exception as e:
        await db.rollback()
        raise ApiError("Failed to publish or flag post")

    if author_user and author_user.email:
        try:
            from core.email_service import send_post_review_email

            await send_post_review_email(author_user.email, status, author_full_name)
        except Exception as e:
            logger.exception("Failed to queue post review email: %s", e)

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
        if visibility == "private":
            raise ApiError("Post is not accessible")
        if visibility == "connections_only":
            from apps.connections.services.connection_service import are_connected
            if not await are_connected(db, user_id, post.author_user_id):
                raise ApiError("Post is not accessible")

    return post

async def list_draft_posts_service(
    user_id: UUID,
    db: AsyncSession,
) -> list[Post]:
    """Return draft posts owned by the authenticated user."""
    stmt = (
        select(Post)
        .where(Post.author_user_id == user_id, Post.state == PostState.draft)
        .order_by(Post.updated_at.desc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def delete_draft_post_service(
    post_id: UUID,
    user_id: UUID,
    db: AsyncSession,
) -> Post:
    """Soft-delete a draft post owned by the authenticated user."""
    result = await db.execute(select(Post).where(Post.id == post_id))
    post = result.scalar_one_or_none()

    if not post:
        raise ApiError("Post not found")
    if post.author_user_id != user_id:
        raise ApiError("Post does not belong to the authenticated user")
    if post.state != PostState.draft:
        raise ApiError("Only draft posts can be deleted through this endpoint")

    post.state = PostState.deleted
    post.updated_at = utc_now()

    try:
        await db.commit()
        await db.refresh(post)
    except Exception:
        await db.rollback()
        raise ApiError("Failed to delete draft post")

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

_LIST_POST_STATES: dict[str, PostState] = {
    "published": PostState.published,
    "processing": PostState.processing,
    "flagged": PostState.flagged,
    "draft": PostState.draft,
}


async def get_profile_visibility_block_message(
    current_user: User,
    target_user_id: UUID | None,
    db: AsyncSession,
) -> str | None:
    """Return a success-response message when profile visibility blocks post listing."""
    if target_user_id is None or target_user_id == current_user.id:
        return None

    role = current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)
    if role == "superadmin":
        return None

    from apps.connections.services.connection_service import are_connected
    from apps.profiles.db_models import Profile
    from common.enums import ProfileVisibility

    profile = (
        await db.execute(select(Profile).where(Profile.user_id == target_user_id))
    ).scalar_one_or_none()
    if not profile:
        return None

    visibility = (
        profile.profile_visibility.value
        if hasattr(profile.profile_visibility, "value")
        else str(profile.profile_visibility)
    )
    if visibility == ProfileVisibility.private.value:
        return "Profile visibility is private"
    if visibility == ProfileVisibility.connections_only.value:
        if not await are_connected(db, current_user.id, target_user_id):
            return "profile is connection_only"
    return None


async def list_user_posts_service(
    current_user: User,
    db: AsyncSession,
    target_user_id: UUID | None = None,
    state: str = "published",
    page: int | None = None,
    page_size: int | None = None,
) -> tuple[list[Post], int]:
    """
    List posts by state for the current user or, for superadmins, any/all users.
    """
    from apps.feed.repositories.post_repository import (
        fetch_posts_by_state,
        count_posts_by_state,
        user_exists,
    )

    requested_state = _LIST_POST_STATES.get(state)
    if requested_state is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid state. Allowed values: published, processing, flagged, draft",
        )

    role = current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)
    is_superadmin = role == "superadmin"
    effective_user_id = target_user_id

    if not is_superadmin:
        if (
            target_user_id is not None
            and target_user_id != current_user.id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        effective_user_id = target_user_id or current_user.id

    if effective_user_id is not None and not await user_exists(db, effective_user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    total_items = await count_posts_by_state(db, state=requested_state, user_id=effective_user_id)

    if page is None and page_size is None:
        offset = 0
        limit = None
    else:
        p = page or 1
        ps = page_size or 20
        offset = (p - 1) * ps
        limit = ps

    posts = await fetch_posts_by_state(
        db,
        state=requested_state,
        user_id=effective_user_id,
        offset=offset,
        limit=limit,
    )
    return posts, total_items


def _format_reviewed_post_media(post: Post) -> list[dict]:
    media_data = []
    if post.attachments:
        for attachment in post.attachments:
            asset = attachment.media_asset
            if asset:
                media_data.append({
                    "url": generate_download_url(asset.key),
                    "type": asset.type.value if hasattr(asset.type, "value") else str(asset.type),
                    "mime_type": asset.mime_type,
                    "original_filename": asset.original_filename,
                    "file_size": asset.file_size,
                    "key": asset.key,
                })
    return media_data


def _review_status_from_post(post: Post) -> str:
    if post.state == PostState.flagged:
        return "flag"
    return "publish"


def _format_reviewed_post_item(post: Post, profile, moderator_user=None, moderator_profile=None) -> dict:
    content = post.content or {}
    
    moderator_name = None
    if post.moderator_id is not None:
        if moderator_profile:
            from apps.profiles.services.response_service import _compose_full_name
            moderator_name = _compose_full_name(moderator_profile.first_name, moderator_profile.last_name)
        if not moderator_name and moderator_user:
            moderator_name = moderator_user.email
        if not moderator_name:
            moderator_name = "Moderator"

    return {
        "id": post.id,
        "user_id": post.author_user_id,
        "caption": content.get("caption"),
        "content_html": content.get("content_html") or "",
        "review_status": _review_status_from_post(post),
        "is_moderator_reviewed": post.is_moderator_reviewed,
        "reviewed_at": post.reviewed_at,
        "created_at": post.created_at,
        "moderator_id": post.moderator_id,
        "moderator_name": moderator_name,
        "profile_photo_url": (
            generate_profile_image_url(profile.profile_photo_url)
            if profile and profile.profile_photo_url
            else None
        ),
        "first_name": profile.first_name if profile else None,
        "last_name": profile.last_name if profile else None,
        "media": _format_reviewed_post_media(post),
    }


def _format_processing_post_item(post: Post, profile, mod_user=None, mod_profile=None) -> dict:
    content = post.content or {}
    moderator_name = None
    if mod_profile:
        parts = [part for part in (mod_profile.first_name, mod_profile.last_name) if part]
        moderator_name = " ".join(parts).strip() or None
    if not moderator_name and mod_user:
        moderator_name = mod_user.email

    return {
        "user_id": post.author_user_id,
        "first_name": profile.first_name if profile else None,
        "last_name": profile.last_name if profile else None,
        "profile_photo_url": (
            generate_profile_image_url(profile.profile_photo_url)
            if profile and profile.profile_photo_url
            else None
        ),
        "post_id": post.id,
        "caption": content.get("caption"),
        "content_html": content.get("content_html"),
        "media": _extract_post_media(post),
        "is_moderator_reviewed": post.is_moderator_reviewed,
        "moderator_id": post.moderator_id,
        "moderator_name": moderator_name,
        "reviewed_at": post.reviewed_at,
    }


async def list_processing_posts_service(
    db: AsyncSession,
    moderator_id: UUID | None = None,
    page: int | None = None,
    page_size: int | None = None,
) -> dict:
    from sqlalchemy import func
    from sqlalchemy.orm import selectinload, aliased
    from apps.feed.db_models import PostAttachment
    from apps.profiles.db_models import Profile
    from apps.accounts.db_models import User
    from common.pagination import build_paginated_response

    filters = [Post.state == PostState.processing]
    if moderator_id is not None:
        filters.append(Post.moderator_id == moderator_id)

    total_items = int((await db.execute(select(func.count(Post.id)).where(*filters))).scalar_one())

    mod_user_alias = aliased(User)
    mod_profile_alias = aliased(Profile)

    stmt = (
        select(Post, Profile, mod_user_alias, mod_profile_alias)
        .outerjoin(Profile, Profile.user_id == Post.author_user_id)
        .outerjoin(mod_user_alias, mod_user_alias.id == Post.moderator_id)
        .outerjoin(mod_profile_alias, mod_profile_alias.user_id == Post.moderator_id)
        .where(*filters)
        .options(selectinload(Post.attachments).selectinload(PostAttachment.media_asset))
        .order_by(Post.created_at.desc())
    )
    if page is not None and page_size is not None:
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)

    rows = (await db.execute(stmt)).all()
    items = [
        _format_processing_post_item(post, profile, mod_user, mod_profile)
        for post, profile, mod_user, mod_profile in rows
    ]

    p = page or 1
    ps = page_size if page_size is not None else (len(items) if items else 1)
    return build_paginated_response(items, p, ps, total_items).model_dump()


async def list_reviewed_posts_service(
    db: AsyncSession,
    moderator_id: UUID | None,
    status: Literal["publish", "flag"] | None = None,
    page: int | None = None,
    page_size: int | None = None,
) -> dict:
    from common.pagination import build_paginated_response
    from apps.feed.repositories.post_repository import (
        count_reviewed_posts_for_moderator,
        fetch_reviewed_posts_for_moderator,
    )

    total_items = await count_reviewed_posts_for_moderator(db, moderator_id, status)

    p = page or 1
    if page is None and page_size is None:
        ps = total_items if total_items > 0 else 1
        offset = 0
        limit = None
    else:
        ps = page_size or 20
        offset = (p - 1) * ps
        limit = ps

    posts = await fetch_reviewed_posts_for_moderator(
        db,
        moderator_id,
        status=status,
        offset=offset,
        limit=limit,
    )
    formatted = [
        _format_reviewed_post_item(post, profile, mod_user, mod_profile)
        for post, profile, mod_user, mod_profile in posts
    ]
    return build_paginated_response(formatted, p, ps, total_items).model_dump()
