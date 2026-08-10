from __future__ import annotations
import logging
from typing import Literal
from datetime import datetime, timezone
from uuid import UUID
from fastapi import HTTPException, status
from common.exceptions import ApiError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select, update
from common.enums import FEED_VISIBLE_POST_STATES, PostState, ReportEntityType
from apps.accounts.db_models import User
from apps.feed.db_models import (
    LinkPreview,
    MediaAsset,
    Post,
    PostAttachment,
    PostHashtag,
    PostRevision,
    PostTopic,
)
from apps.feed.schemas import SavePostRequest, EditPostRequest
from apps.engagement.schemas import PostReactionsGrouped
from apps.feed.content_utils import sanitize_html, validate_content, validate_media_count
from core.images import generate_download_url, generate_profile_image_url
from apps.connections.services.connection_service import is_blocked

from .media_service import _verify_and_attach_media
from apps.recommendations.services.post_keyword_service import log_post_keywords_best_effort

from .revision_service import _build_content_dict, _create_revision, _sync_hashtags
from apps.moderation.services.moderator_assignment_service import (
    assign_next_moderator_round_robin,
    _fetch_active_moderator_ids,
    _fetch_superadmin_user_ids,
)

logger = logging.getLogger(__name__)

_COUNTED_POST_STATES = (PostState.published, PostState.reinstate)


async def _hard_delete_post(db: AsyncSession, post: Post) -> UUID:
    """
    Permanently remove a post and its dependent rows from the database.

    Explicit deletes are required because not all FKs use ON DELETE CASCADE.
    """
    from apps.engagement.db_models import (
        Bookmark,
        Comment,
        CommentReaction,
        PostReaction,
        Repost,
        ShareEvent,
    )
    from apps.report.db_models import Report

    post_id = post.id
    comment_ids_subq = select(Comment.id).where(Comment.post_id == post_id)

    await db.execute(
        delete(CommentReaction).where(CommentReaction.comment_id.in_(comment_ids_subq))
    )
    await db.execute(
        delete(Report).where(
            Report.entity_type == ReportEntityType.comment,
            Report.entity_id.in_(comment_ids_subq),
        )
    )
    # Clear self-FK so nested comments can be removed in one pass.
    await db.execute(
        update(Comment).where(Comment.post_id == post_id).values(parent_comment_id=None)
    )
    await db.execute(delete(Comment).where(Comment.post_id == post_id))

    await db.execute(delete(PostReaction).where(PostReaction.post_id == post_id))
    await db.execute(delete(Bookmark).where(Bookmark.post_id == post_id))
    await db.execute(delete(Repost).where(Repost.post_id == post_id))
    await db.execute(delete(ShareEvent).where(ShareEvent.post_id == post_id))
    await db.execute(delete(PostRevision).where(PostRevision.post_id == post_id))
    await db.execute(delete(PostHashtag).where(PostHashtag.post_id == post_id))
    await db.execute(delete(PostTopic).where(PostTopic.post_id == post_id))
    await db.execute(delete(LinkPreview).where(LinkPreview.post_id == post_id))

    media_asset_ids = list(
        (
            await db.execute(
                select(PostAttachment.media_asset_id).where(PostAttachment.post_id == post_id)
            )
        )
        .scalars()
        .all()
    )
    await db.execute(delete(PostAttachment).where(PostAttachment.post_id == post_id))

    if media_asset_ids:
        still_referenced = set(
            (
                await db.execute(
                    select(PostAttachment.media_asset_id).where(
                        PostAttachment.media_asset_id.in_(media_asset_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        orphan_ids = [mid for mid in media_asset_ids if mid not in still_referenced]
        if orphan_ids:
            await db.execute(delete(MediaAsset).where(MediaAsset.id.in_(orphan_ids)))

    await db.execute(
        delete(Report).where(
            Report.entity_type == ReportEntityType.post,
            Report.entity_id == post_id,
        )
    )
    await db.execute(delete(Post).where(Post.id == post_id))
    return post_id


async def _capture_user_topics(db: AsyncSession, user_id: UUID) -> set[str]:
    from apps.profiles.db_models.profile_db_model import Profile
    from apps.notifications.services.topic_service import TopicService

    profile = (
        await db.execute(select(Profile).where(Profile.user_id == user_id))
    ).scalar_one_or_none()
    if profile is None:
        return set()
    return await TopicService.capture_topics(db, profile)


async def _sync_user_topics_best_effort(
    db: AsyncSession,
    user_id: UUID,
    *,
    old_topics: set[str],
) -> None:
    from apps.profiles.db_models.profile_db_model import Profile
    from apps.notifications.services.topic_service import TopicService

    try:
        profile = (
            await db.execute(select(Profile).where(Profile.user_id == user_id))
        ).scalar_one_or_none()
        if profile is None:
            return
        await TopicService.sync_user_topics(
            db,
            user_id,
            old_topics=old_topics,
            profile=profile,
        )
    except Exception:
        logger.exception(
            "Failed to sync Firebase topics after post change user_id=%s",
            user_id,
        )


def _should_sync_topics_for_post_state(
    post_state: PostState,
    *,
    previous_state: PostState | None = None,
) -> bool:
    if post_state in FEED_VISIBLE_POST_STATES:
        return True
    return previous_state in FEED_VISIBLE_POST_STATES


def _should_sync_topics_for_post(
    post_state: PostState,
    *,
    previous_state: PostState | None = None,
    hashtag_content_changed: bool = False,
) -> bool:
    if _should_sync_topics_for_post_state(post_state, previous_state=previous_state):
        return True
    return hashtag_content_changed and (
        post_state in FEED_VISIBLE_POST_STATES
        or previous_state in FEED_VISIBLE_POST_STATES
    )



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

def _resolve_person_name(profile=None, user=None) -> str | None:
    if profile:
        parts = [part for part in (profile.first_name, profile.last_name) if part]
        name = " ".join(parts).strip()
        if name:
            return name
    if user:
        email = getattr(user, "email", None)
        if email:
            return email
    return None


def _resolve_moderator_name(mod_user=None, mod_profile=None) -> str | None:
    return _resolve_person_name(mod_profile, mod_user)


def _normalize_profile_visibility(profile) -> str:
    """Return 'private' only when visibility is private; otherwise 'public'.

    ``connections_only`` and ``None`` both map to ``public`` for API responses.
    """
    if profile is None:
        return "public"
    raw = getattr(profile, "profile_visibility", None)
    if raw is None:
        return "public"
    value = raw.value if hasattr(raw, "value") else str(raw)
    return "private" if value == "private" else "public"


def format_post_detail(
    post: Post,
    *,
    author_profile=None,
    author_user=None,
    moderator_user=None,
    moderator_profile=None,
    is_connected: bool | None = None,
    is_requested: bool | None = None,
    profile_details: dict | None = None,
    is_liked: bool = False,
    is_reposted: bool = False,
    is_bookmarked: bool = False,
    user_reaction: str | None = None,
    reactions=None,
    reposted_data: dict | None = None,
    viewer_user_id: UUID | None = None,
) -> dict:
    """
    Format a Post model and its attachments into a dictionary matching PostDetailData schema.

    ``profile_visibility`` is always the post author's visibility (never mixed with
    a reposter). Set last so enrichment cannot overwrite it.
    """
    media_data = _extract_post_media(post)

    content = post.content or {}
    state_value = post.state.value if hasattr(post.state, "value") else str(post.state)
    is_repostable = (
        True
        if viewer_user_id is None
        else viewer_user_id != post.author_user_id
    )
    author_profile = author_profile or getattr(post, "_author_profile", None)
    author_user = author_user or getattr(post, "_author_user", None)
    data = {
        "id": post.id,
        "author_user_id": post.author_user_id,
        "author_name": _resolve_person_name(author_profile, author_user),
        "state": state_value,
        "status": state_value,
        "revision_number": post.revision_number,
        "content": {
            "caption": content.get("caption"),
            "content_html": content.get("content_html"),
            "visibility": content.get("visibility", "public"),
        },
        "created_at": post.created_at,
        "updated_at": post.updated_at,
        "is_edited": bool(getattr(post, "is_edited", False)),
        "like_count": post.like_count,
        "repost_count": post.repost_count,
        "share_count": getattr(post, "share_count", 0),
        "comment_count": getattr(post, "comment_count", 0),
        "is_liked": is_liked,
        "is_reposted": is_reposted,
        "is_bookmarked": is_bookmarked,
        "is_repostable": is_repostable,
        "user_reaction": user_reaction,
        "reactions": (
            reactions.model_dump()
            if isinstance(reactions, PostReactionsGrouped)
            else reactions
            if reactions is not None
            else PostReactionsGrouped().model_dump()
        ),
        "is_moderator_reviewed": post.is_moderator_reviewed,
        "reviewed_at": post.reviewed_at,
        "moderator_id": post.moderator_id,
        "moderator_name": _resolve_moderator_name(moderator_user, moderator_profile),
        "moderation_notes": getattr(post, "moderation_notes", None),
        "media": media_data,
        "reposted_data": reposted_data,
    }
    if author_profile is not None:
        photo_url = (
            generate_profile_image_url(author_profile.profile_photo_url)
            if author_profile.profile_photo_url
            else None
        )
        data.update({
            "first_name": author_profile.first_name,
            "last_name": author_profile.last_name,
            "profilePhoto_url": photo_url,
        })
    if is_connected is not None:
        data["is_connected"] = is_connected
    if is_requested is not None:
        data["is_requested"] = is_requested
    if profile_details is not None:
        # Strip any accidental visibility key so author visibility stays authoritative.
        details = {k: v for k, v in profile_details.items() if k != "profile_visibility"}
        data.update(details)
    # Author's profile_visibility only — set last to avoid duplicates/overwrites.
    data["profile_visibility"] = _normalize_profile_visibility(author_profile)
    return data


def format_repost_item(
    original_post: Post,
    *,
    original_author_profile,
    reposter_profile,
    repost_id: UUID,
    reposted_at: datetime,
    original_author_is_connected: bool | None = None,
    original_author_is_requested: bool | None = None,
    reposter_is_connected: bool | None = None,
    reposter_is_requested: bool | None = None,
    original_author_details: dict | None = None,
    reposter_details: dict | None = None,
    is_liked: bool = False,
    viewer_has_reposted: bool = False,
    is_bookmarked: bool = False,
    user_reaction: str | None = None,
    reactions=None,
    moderator_user=None,
    moderator_profile=None,
    viewer_user_id: UUID | None = None,
) -> dict:
    """
    Format a repost as a common post object for the reposter, with the original
    post nested under ``reposted_data`` only (not mixed into the outer object).

    Visibility ownership:
    - top-level ``profile_visibility`` -> reposting user
    - ``reposted_data.profile_visibility`` -> original post author
    """
    nested = format_post_detail(
        original_post,
        author_profile=original_author_profile,
        moderator_user=moderator_user,
        moderator_profile=moderator_profile,
        is_connected=original_author_is_connected,
        is_requested=original_author_is_requested,
        profile_details=original_author_details,
        is_liked=is_liked,
        is_reposted=viewer_has_reposted,
        is_bookmarked=is_bookmarked,
        user_reaction=user_reaction,
        reactions=reactions,
        reposted_data=None,
        viewer_user_id=viewer_user_id,
    )
    # Original author visibility lives only under reposted_data.
    nested["profile_visibility"] = _normalize_profile_visibility(original_author_profile)

    rp_photo = None
    if reposter_profile is not None and reposter_profile.profile_photo_url:
        rp_photo = generate_profile_image_url(reposter_profile.profile_photo_url)

    reactions_payload = (
        reactions.model_dump()
        if isinstance(reactions, PostReactionsGrouped)
        else reactions
        if reactions is not None
        else PostReactionsGrouped().model_dump()
    )

    reposter_user_id = getattr(reposter_profile, "user_id", None)
    # Outer card author is the reposter; nested uses the original author.
    is_repostable = (
        True
        if viewer_user_id is None
        else viewer_user_id != original_post.author_user_id
    )

    data = {
        "id": repost_id,
        "author_user_id": reposter_user_id,
        "first_name": getattr(reposter_profile, "first_name", None) if reposter_profile else None,
        "last_name": getattr(reposter_profile, "last_name", None) if reposter_profile else None,
        "profilePhoto_url": rp_photo,
        "state": "published",
        "status": "published",
        "revision_number": 0,
        "content": {
            "caption": None,
            "content_html": None,
            "visibility": "public",
        },
        "created_at": reposted_at,
        "updated_at": reposted_at,
        "is_edited": False,
        "like_count": original_post.like_count,
        "repost_count": original_post.repost_count,
        "share_count": getattr(original_post, "share_count", 0),
        "comment_count": getattr(original_post, "comment_count", 0),
        "is_liked": is_liked,
        "is_reposted": True,
        "is_bookmarked": is_bookmarked,
        "is_repostable": is_repostable,
        "user_reaction": user_reaction,
        "reactions": reactions_payload,
        "is_moderator_reviewed": original_post.is_moderator_reviewed,
        "reviewed_at": original_post.reviewed_at,
        "moderator_id": original_post.moderator_id,
        "moderator_name": _resolve_moderator_name(moderator_user, moderator_profile),
        "media": [],
        "reposted_data": nested,
    }
    if reposter_is_connected is not None:
        data["is_connected"] = reposter_is_connected
    if reposter_is_requested is not None:
        data["is_requested"] = reposter_is_requested
    if reposter_details is not None:
        details = {k: v for k, v in reposter_details.items() if k != "profile_visibility"}
        data.update(details)
    # Reposter visibility only at top level — set last to avoid duplicates/overwrites.
    data["profile_visibility"] = _normalize_profile_visibility(reposter_profile)
    return data

# Post states that require a moderator to be assigned for review.
_MODERATION_STATES = (PostState.processing, PostState.published)


async def _assign_fallback_moderator(post: Post, db: AsyncSession) -> None:
    """Assign the first available moderator when round-robin is unavailable."""
    moderator_ids = await _fetch_active_moderator_ids(db)
    if not moderator_ids:
        return
    post.moderator_id = moderator_ids[0]
    await db.flush()
    logger.warning(
        "Assigned fallback moderator %s to post %s (state=%s)",
        post.moderator_id,
        post.id,
        post.state,
    )


async def _assign_moderator_for_review(
    post: Post,
    db: AsyncSession,
    *,
    previous_state: PostState | None = None,
) -> None:
    """Assign a moderator via round-robin when a post enters a review state.

    A moderator is assigned whenever a post transitions into ``processing`` or
    ``published`` and does not already have one. Assignment is skipped if a
    moderator is already set so an existing assignment is never overwritten.
    """
    if post.state not in _MODERATION_STATES:
        return
    if post.moderator_id is not None:
        return
    try:
        post.moderator_id = await assign_next_moderator_round_robin(db)
        await db.flush()
    except ApiError as exc:
        logger.error(
            "Round-robin unavailable for post %s (state=%s): %s",
            post.id,
            post.state,
            exc,
        )
        await _assign_fallback_moderator(post, db)
    except Exception:
        logger.exception(
            "Unexpected error assigning moderator to post %s (state=%s)",
            post.id,
            post.state,
        )
        await db.rollback()
        refreshed = (
            await db.execute(select(Post).where(Post.id == post.id))
        ).scalar_one_or_none()
        if refreshed is not None and refreshed.moderator_id is None:
            await _assign_fallback_moderator(refreshed, db)
            post.moderator_id = refreshed.moderator_id


async def _repair_unassigned_moderators_for_state(
    db: AsyncSession,
    *,
    status: Literal[
        "published", "flagged", "rejected", "reinstate", "escalate", "processing"
    ]
    | None = None,
    limit: int = 500,
) -> None:
    """Assign moderators to posts in a review state that are still unassigned."""
    from apps.feed.repositories.post_repository import _reviewed_states_for_status

    for target_state in _reviewed_states_for_status(status):
        await _repair_unassigned_moderators(db, target_state=target_state, limit=limit)


async def _repair_unassigned_moderators(
    db: AsyncSession,
    *,
    target_state: PostState,
    limit: int = 500,
) -> None:
    """
    Repair broken moderator assignments for posts awaiting review.

    - ``moderator_id IS NULL`` → round-robin to an active moderator.
    - ``moderator_id`` points to a deleted/inactive/non-holder user → Super Admin.
    - Valid active moderator or Super Admin assignments are left unchanged.
    """
    from sqlalchemy import and_, exists, or_
    from apps.accounts.db_models import UserRole, Role
    from common.enums import UserStatus

    superadmin_ids = await _fetch_superadmin_user_ids(db)
    fallback_superadmin_id = superadmin_ids[0] if superadmin_ids else None

    valid_holder = exists(
        select(1)
        .select_from(User)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(
            User.id == Post.moderator_id,
            User.is_deleted.is_(False),
            User.status == UserStatus.active,
            Role.name.in_(["moderator", "superadmin"]),
        )
    )

    needs_repair = or_(
        Post.moderator_id.is_(None),
        and_(Post.moderator_id.isnot(None), ~valid_holder),
    )

    result = await db.execute(
        select(Post)
        .where(
            Post.state == target_state,
            needs_repair,
        )
        .order_by(Post.created_at.desc())
        .limit(limit)
    )
    posts = list(result.scalars().all())
    if not posts:
        return

    now = utc_now()
    for post in posts:
        try:
            old_moderator_id = post.moderator_id
            if post.moderator_id is None:
                await _assign_moderator_for_review(post, db)
            elif fallback_superadmin_id is not None:
                # e.g. departing moderator was soft-deleted; keep queue with Super Admin.
                post.moderator_id = fallback_superadmin_id
                post.updated_at = now
                db.add(post)
            else:
                await _assign_moderator_for_review(post, db)

            if (
                post.moderator_id is not None
                and post.moderator_id != old_moderator_id
            ):
                from apps.report.repositories.report_repository import (
                    sync_open_report_moderator_for_post,
                )

                await sync_open_report_moderator_for_post(
                    db,
                    post_id=post.id,
                    moderator_id=post.moderator_id,
                )

            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("Failed to repair moderator assignment for post %s", post.id)


async def _soft_delete_other_drafts(
    db: AsyncSession,
    user_id: UUID,
    *,
    exclude_post_id: UUID | None = None,
) -> None:
    """Hard-delete other draft posts for the user (replacing a draft)."""
    stmt = select(Post).where(
        Post.author_user_id == user_id,
        Post.state == PostState.draft,
    )
    if exclude_post_id is not None:
        stmt = stmt.where(Post.id != exclude_post_id)

    result = await db.execute(stmt)
    for draft in list(result.scalars().all()):
        await _hard_delete_post(db, draft)

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
    post_state = PostState.draft if payload.is_draft else PostState.published

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

            old_topics = (
                await _capture_user_topics(db, user_id)
                if _should_sync_topics_for_post_state(post_state)
                else set()
            )
            await _sync_hashtags(post.id, content_dict, db)
            await _create_revision(post, user_id, db, previous_state=None)
            await _assign_moderator_for_review(post, db)

            await db.commit()
            await db.refresh(post)
            logger.info(
                "[post-keyword-extraction]\nPost created\npost_id=%s\nuser_id=%s\nstate=%s",
                post.id,
                user_id,
                post.state.value if hasattr(post.state, "value") else post.state,
            )
            await log_post_keywords_best_effort(post.id, content_dict, user_id=user_id, db=db)
            if _should_sync_topics_for_post_state(post_state):
                await _sync_user_topics_best_effort(db, user_id, old_topics=old_topics)
        except ApiError:
            await db.rollback()
            raise
        except Exception as e:
            await db.rollback()
            raise ApiError("Failed to create post")

        # Increment posts_count after a successful commit so a stats failure
        # never rolls back the post creation itself.
        if post_state == PostState.published:
            try:
                from apps.profiles.services.profile_stats_service import increment_posts_count_for_user

                await increment_posts_count_for_user(db, user_id)
                await db.commit()
            except Exception:
                logger.exception(
                    "Failed to update posts_count for user %s after creating post %s",
                    user_id,
                    post.id,
                )

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

        old_topics = (
            await _capture_user_topics(db, user_id)
            if _should_sync_topics_for_post_state(post_state, previous_state=previous_state)
            else set()
        )
        await _sync_hashtags(post.id, content_dict, db)
        await _create_revision(post, user_id, db, previous_state=previous_state)
        await _assign_moderator_for_review(
            post, db, previous_state=previous_state
        )

        await db.commit()
        await db.refresh(post)
        logger.info(
            "[post-keyword-extraction]\nPost created\npost_id=%s\nuser_id=%s\nstate=%s",
            post.id,
            user_id,
            post.state.value if hasattr(post.state, "value") else post.state,
        )
        await log_post_keywords_best_effort(post.id, content_dict, user_id=user_id, db=db)
        if _should_sync_topics_for_post_state(post_state, previous_state=previous_state):
            await _sync_user_topics_best_effort(db, user_id, old_topics=old_topics)
    except ApiError:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise ApiError("Failed to update post")

    # Increment posts_count when a draft/processing post is first published.
    # Isolated after commit so a stats failure never rolls back the post update.
    if previous_state != PostState.published and post_state == PostState.published:
        try:
            from apps.profiles.services.profile_stats_service import increment_posts_count_for_user

            await increment_posts_count_for_user(db, user_id)
            await db.commit()
        except Exception:
            logger.exception(
                "Failed to update posts_count for user %s after updating post %s",
                user_id,
                post.id,
            )

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

    previous_state = post.state

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

    # Flagged posts re-enter moderation as processing after the author edits.
    # Keep the previously assigned moderator so the same reviewer gets the update.
    if previous_state == PostState.flagged and post.state == PostState.flagged:
        post.state = PostState.processing
        post.is_moderator_reviewed = False
        post.reviewed_at = None

    post.revision_number += 1
    post.is_edited = True
    post.updated_at = utc_now()

    hashtag_content_changed = payload.content is not None and (
        payload.content.caption is not None or payload.content.content_html is not None
    )
    should_sync_topics = _should_sync_topics_for_post(
        post.state,
        previous_state=previous_state,
        hashtag_content_changed=hashtag_content_changed,
    )

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

        old_topics = (
            await _capture_user_topics(db, user_id) if should_sync_topics else set()
        )
        # Re-sync hashtags from current caption and content_html
        await _sync_hashtags(post.id, merged_content, db)

        # Create revision audit record
        await _create_revision(post, user_id, db, previous_state=previous_state)

        # Re-enter review queue without overwriting an existing moderator assignment.
        await _assign_moderator_for_review(
            post, db, previous_state=previous_state
        )

        await db.commit()
        await db.refresh(post)
        logger.info(
            "[post-keyword-extraction]\nPost created\npost_id=%s\nuser_id=%s\nstate=%s",
            post.id,
            user_id,
            post.state.value if hasattr(post.state, "value") else post.state,
        )
        await log_post_keywords_best_effort(post.id, merged_content, user_id=user_id, db=db)
        if should_sync_topics:
            await _sync_user_topics_best_effort(db, user_id, old_topics=old_topics)
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

    old_topics = (
        await _capture_user_topics(db, user_id)
        if _should_sync_topics_for_post_state(post.state, previous_state=previous_state)
        else set()
    )

    # Increment revision number and update timestamp
    post.revision_number += 1
    post.updated_at = utc_now()

    try:
        # Assign a moderator via round-robin when a post enters a review state
        # (published/processing) and is not already assigned.
        await _assign_moderator_for_review(post, db, previous_state=previous_state)

        # Create revision audit record
        await _create_revision(post, user_id, db, previous_state=previous_state)

        await db.commit()
        await db.refresh(post)
        logger.info(
            "[post-keyword-extraction]\nPost published\npost_id=%s\nuser_id=%s\nstate=%s",
            post.id,
            user_id,
            post.state.value if hasattr(post.state, "value") else post.state,
        )
        await log_post_keywords_best_effort(
            post.id,
            post.content,
            user_id=user_id,
            db=db,
        )
        if _should_sync_topics_for_post_state(post.state, previous_state=previous_state):
            await _sync_user_topics_best_effort(db, user_id, old_topics=old_topics)
    except Exception as e:
        await db.rollback()
        raise ApiError("Failed to publish post")

    # Increment posts_count after a successful commit so a stats failure
    # never rolls back the post state change.
    if previous_state != PostState.published and post.state == PostState.published:
        try:
            from apps.profiles.services.profile_stats_service import increment_posts_count_for_user

            await increment_posts_count_for_user(db, post.author_user_id)
            await db.commit()
        except Exception:
            logger.exception(
                "Failed to update posts_count for user %s after publishing post %s",
                post.author_user_id,
                post.id,
            )

    return post

async def admin_publish_post_service(
    post_id: UUID,
    status: Literal["published", "flagged", "rejected", "reinstate", "escalate"],
    admin_user_id: UUID,
    db: AsyncSession,
    *,
    notes: str | None = None,
) -> Post | dict:
    """
    Moderate a post by setting its lifecycle state.

    ``status`` maps to ``Post.state`` except ``rejected``, which hard-deletes:
    - ``published`` -> published (or hidden when visibility is private/hidden)
    - ``flagged``   -> flagged
    - ``rejected``  -> permanently remove the post from the DB
    - ``reinstate`` -> reinstate
    - ``escalate``  -> escalate (reassigned to a superadmin)
    """
    from apps.profiles.db_models import Profile

    result = await db.execute(select(Post).where(Post.id == post_id))
    post = result.scalar_one_or_none()

    if not post:
        raise ApiError("Post not found")

    if status == "rejected":
        previous_state = post.state
        author_user_id = post.author_user_id
        rejected_post_id = post.id
        was_counted = previous_state in _COUNTED_POST_STATES
        try:
            from apps.moderation.services import record_moderation_history
            from common.enums import ReportEntityType

            await record_moderation_history(
                db,
                entity_type=ReportEntityType.post,
                entity_id=rejected_post_id,
                action="rejected",
                moderator_id=admin_user_id,
                comment=notes.strip() if notes else None,
            )
            deleted_id = await _hard_delete_post(db, post)
            await db.commit()
        except Exception:
            await db.rollback()
            raise ApiError("Failed to reject and delete post")

        if was_counted:
            try:
                from apps.profiles.services.profile_stats_service import (
                    decrement_posts_count_for_user,
                )

                await decrement_posts_count_for_user(db, author_user_id)
                await db.commit()
            except Exception:
                logger.exception(
                    "Failed to update posts_count for user %s after rejecting post %s",
                    author_user_id,
                    deleted_id,
                )

        try:
            from apps.notifications.services import POST_REJECTED, notify_post_author

            await notify_post_author(
                db,
                post_id=rejected_post_id,
                author_user_id=author_user_id,
                notification_type=POST_REJECTED,
            )
        except Exception:
            logger.exception(
                "Failed to notify author after rejecting post %s",
                rejected_post_id,
            )

        return {"id": deleted_id, "deleted": True, "status": "rejected"}

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
    assignee_id = admin_user_id

    if status in ("published", "reinstate"):
        # Respect visibility stored in content
        visibility = (post.content or {}).get("visibility", "public")
        if status == "published" and (
            visibility in ("private", "hidden") or post.state == PostState.hidden
        ):
            post.state = PostState.hidden
        else:
            post.state = PostState.published if status == "published" else PostState.reinstate
        if notes is not None:
            post.moderation_notes = notes.strip() or None
    elif status == "flagged":
        post.state = PostState.flagged
        if notes is not None:
            post.moderation_notes = notes.strip() or None
    elif status == "escalate":
        superadmin_ids = await _fetch_superadmin_user_ids(db)
        if not superadmin_ids:
            raise ApiError("No superadmin available to escalate this post")
        post.state = PostState.escalate
        if notes is not None:
            post.moderation_notes = notes.strip() or None
        assignee_id = superadmin_ids[0]
    else:
        raise ApiError(f"Invalid status: {status}")

    post.moderator_id = assignee_id
    post.is_moderator_reviewed = True
    post.reviewed_at = utc_now()

    # Increment revision number and update timestamp
    post.revision_number += 1
    post.updated_at = utc_now()

    # Profile posts_count tracks posts that are publicly countable for the author.
    # Flagged/escalate leave that set; publishing/reinstating re-enters it.
    was_counted = previous_state in _COUNTED_POST_STATES
    now_counted = post.state in _COUNTED_POST_STATES

    try:
        # Create revision audit record with the acting admin as the editor
        await _create_revision(post, admin_user_id, db, previous_state=previous_state)

        from apps.moderation.services import record_moderation_history
        from common.enums import ReportEntityType

        await record_moderation_history(
            db,
            entity_type=ReportEntityType.post,
            entity_id=post.id,
            action=status,
            moderator_id=admin_user_id,
            comment=notes.strip() if notes else None,
        )

        await db.commit()
        await db.refresh(post)
    except Exception as e:
        await db.rollback()
        raise ApiError("Failed to publish or flag post")

    # Adjust the author's posts_count after a successful state change.
    # Isolated so a stats failure does not roll back the moderation decision.
    try:
        from apps.profiles.services.profile_stats_service import (
            decrement_posts_count_for_user,
            increment_posts_count_for_user,
        )

        if was_counted and not now_counted:
            await decrement_posts_count_for_user(db, post.author_user_id)
            await db.commit()
        elif not was_counted and now_counted:
            await increment_posts_count_for_user(db, post.author_user_id)
            await db.commit()
    except Exception:
        logger.exception(
            "Failed to update posts_count for user %s after moderating post %s "
            "(previous_state=%s new_state=%s)",
            post.author_user_id,
            post.id,
            previous_state,
            post.state,
        )

    if status in ("flagged", "reinstate"):
        try:
            from apps.notifications.services import (
                POST_FLAGGED,
                POST_REINSTATED,
                notify_post_author,
            )

            notification_type = (
                POST_FLAGGED if status == "flagged" else POST_REINSTATED
            )
            await notify_post_author(
                db,
                post_id=post.id,
                author_user_id=post.author_user_id,
                notification_type=notification_type,
            )
        except Exception:
            logger.exception(
                "Failed to notify author after moderating post %s status=%s",
                post.id,
                status,
            )

    # Temporarily disabled: post moderated/published/flagged email
    # if author_user and author_user.email:
    #     try:
    #         from core.email_service import send_post_review_email
    #
    #         await send_post_review_email(author_user.email, status, author_full_name)
    #     except Exception as e:
    #         logger.exception("Failed to queue post review email: %s", e)

    return post

async def get_post_service(
    post_id: UUID,
    user_id: UUID,
    db: AsyncSession,
    *,
    viewer_role: str | None = None,
) -> Post:
    """
    Retrieve details of a specific post.
    Validates visibility access permissions.
    Moderators and superadmins can view posts regardless of feed visibility rules.
    """
    from common.user_visibility import is_hidden_account_status

    result = await db.execute(
        select(Post, User)
        .join(User, User.id == Post.author_user_id)
        .where(Post.id == post_id)
    )
    row = result.one_or_none()
    if not row:
        raise ApiError("Post not found")
    post, author = row

    if viewer_role in ("moderator", "superadmin"):
        return post

    # Hide posts from suspended/banned/deleting authors for other viewers.
    if post.author_user_id != user_id and is_hidden_account_status(author.status):
        raise ApiError("Post not found")
    if post.author_user_id != user_id and (
        author.is_deleted or author.deleted_at is not None
    ):
        raise ApiError("Post not found")

    if post.state == PostState.deleted:
        if post.author_user_id != user_id:
            raise ApiError("Post not found")
        return post

    if post.state in (PostState.flagged, PostState.escalate, PostState.rejected) and post.author_user_id != user_id:
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


async def build_post_detail_response(
    db: AsyncSession,
    post: Post,
    *,
    viewer_user_id: UUID | None = None,
) -> dict:
    """Load author and moderator context, then format a single post for API responses."""
    from sqlalchemy.orm import aliased

    from apps.accounts.db_models import User
    from apps.profiles.db_models import Profile

    author_profile = (
        await db.execute(select(Profile).where(Profile.user_id == post.author_user_id))
    ).scalar_one_or_none()
    author_user = (
        await db.execute(select(User).where(User.id == post.author_user_id))
    ).scalar_one_or_none()

    moderator_user = None
    moderator_profile = None
    if post.moderator_id is not None:
        ModeratorProfile = aliased(Profile)
        row = (
            await db.execute(
                select(User, ModeratorProfile)
                .outerjoin(ModeratorProfile, ModeratorProfile.user_id == User.id)
                .where(User.id == post.moderator_id)
            )
        ).first()
        if row:
            moderator_user, moderator_profile = row

    return format_post_detail(
        post,
        author_profile=author_profile,
        author_user=author_user,
        moderator_user=moderator_user,
        moderator_profile=moderator_profile,
        viewer_user_id=viewer_user_id,
    )


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
) -> dict:
    """Hard-delete a draft post owned by the authenticated user."""
    result = await db.execute(select(Post).where(Post.id == post_id))
    post = result.scalar_one_or_none()

    if not post:
        raise ApiError("Post not found")
    if post.author_user_id != user_id:
        raise ApiError("Post does not belong to the authenticated user")
    if post.state != PostState.draft:
        raise ApiError("Only draft posts can be deleted through this endpoint")

    try:
        deleted_id = await _hard_delete_post(db, post)
        await db.commit()
    except Exception:
        await db.rollback()
        raise ApiError("Failed to delete draft post")

    return {"id": deleted_id, "deleted": True}


async def delete_post_service(
    post_id: UUID,
    user_id: UUID,
    db: AsyncSession
) -> dict:
    """
    Hard-delete a post owned by the authenticated user (row removed from DB).
    """
    result = await db.execute(select(Post).where(Post.id == post_id))
    post = result.scalar_one_or_none()

    if not post:
        raise ApiError("Post not found")
    if post.author_user_id != user_id:
        raise ApiError("Post does not belong to the authenticated user")

    previous_state = post.state
    author_user_id = post.author_user_id
    was_counted = previous_state in _COUNTED_POST_STATES

    try:
        deleted_id = await _hard_delete_post(db, post)
        await db.commit()
    except Exception:
        await db.rollback()
        raise ApiError("Failed to delete post")

    # Profile posts_count tracks published/reinstated posts only.
    if was_counted:
        try:
            from apps.profiles.services.profile_stats_service import (
                decrement_posts_count_for_user,
            )

            await decrement_posts_count_for_user(db, author_user_id)
            await db.commit()
        except Exception:
            logger.exception(
                "Failed to update posts_count for user %s after deleting post %s",
                author_user_id,
                deleted_id,
            )

    return {"id": deleted_id, "deleted": True}

_LIST_POST_STATES: dict[str, PostState] = {
    "published": PostState.published,
    "processing": PostState.processing,
    "flagged": PostState.flagged,
    "draft": PostState.draft,
}

_OWNER_ONLY_LIST_STATES = frozenset({PostState.flagged, PostState.processing})


def _reject_other_user_private_post_states(
    *,
    current_user: User,
    target_user_id: UUID | None,
    requested_state: PostState,
) -> None:
    """Block viewing another user's flagged/processing posts via GET /posts."""
    if target_user_id is None or target_user_id == current_user.id:
        return
    if requested_state in _OWNER_ONLY_LIST_STATES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unauthorized",
        )


def _query_states_for_list(
    requested_state: PostState,
    *,
    is_owner: bool = False,
) -> PostState | tuple[PostState, ...]:
    """
    Resolve which post states to load for GET /posts.

    - Default / ``published``: published + reinstate (public-visible set).
    - ``flagged`` / ``processing``: that state only (owner or superadmin).
    - ``draft``: drafts only.
    Visitors are forced to ``published`` upstream so they never see flagged/processing.
    Each post keeps its real ``state`` / ``status`` (not remapped).
    """
    del is_owner  # kept for call-site compatibility; visitor gating is upstream
    if requested_state == PostState.draft:
        return PostState.draft
    if requested_state == PostState.published:
        return FEED_VISIBLE_POST_STATES
    return requested_state


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
        if not await are_connected(db, current_user.id, target_user_id):
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
    include_total: bool = False,
) -> list[Post] | tuple[list[Post], int]:
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
    is_viewing_other = target_user_id is not None and target_user_id != current_user.id

    _reject_other_user_private_post_states(
        current_user=current_user,
        target_user_id=target_user_id,
        requested_state=requested_state,
    )

    # Regular users viewing another user's posts are restricted to published posts only.
    if is_viewing_other and not is_superadmin:
        requested_state = PostState.published

    if is_superadmin:
        effective_user_id = target_user_id
    else:
        effective_user_id = target_user_id or current_user.id

    if effective_user_id is not None and not await user_exists(db, effective_user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    is_owner = effective_user_id == current_user.id
    query_states = _query_states_for_list(requested_state, is_owner=is_owner)
    total_items = await count_posts_by_state(db, state=query_states, user_id=effective_user_id)

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
        state=query_states,
        user_id=effective_user_id,
        offset=offset,
        limit=limit,
    )
    if include_total:
        return posts, total_items
    return posts


async def list_user_posts_items_service(
    current_user: User,
    db: AsyncSession,
    target_user_id: UUID | None = None,
    state: str = "published",
    page: int | None = None,
    page_size: int | None = None,
) -> tuple[list[dict], int]:
    """List user posts formatted for the API, including moderator assignment fields."""
    from apps.feed.repositories.post_repository import (
        count_posts_by_state,
        fetch_posts_by_state_with_details,
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
    is_viewing_other = target_user_id is not None and target_user_id != current_user.id

    _reject_other_user_private_post_states(
        current_user=current_user,
        target_user_id=target_user_id,
        requested_state=requested_state,
    )

    if is_viewing_other and not is_superadmin:
        requested_state = PostState.published

    if is_superadmin:
        effective_user_id = target_user_id
    else:
        effective_user_id = target_user_id or current_user.id

    if effective_user_id is not None and not await user_exists(db, effective_user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    is_owner = effective_user_id == current_user.id
    query_states = _query_states_for_list(requested_state, is_owner=is_owner)
    total_items = await count_posts_by_state(db, state=query_states, user_id=effective_user_id)

    if page is None and page_size is None:
        offset = 0
        limit = None
    else:
        p = page or 1
        ps = page_size or 20
        offset = (p - 1) * ps
        limit = ps

    rows = await fetch_posts_by_state_with_details(
        db,
        state=query_states,
        user_id=effective_user_id,
        offset=offset,
        limit=limit,
    )

    # Also fetch posts that the effective user has reposted
    from apps.engagement.db_models import Repost
    from apps.profiles.db_models import Profile
    from sqlalchemy import select as sa_select
    from sqlalchemy.orm import selectinload
    from apps.feed.db_models import PostAttachment

    repost_items = []
    include_reposts = (
        requested_state == PostState.published
        and effective_user_id is not None
    )
    if include_reposts:
        # Find all reposts by this user (original post may be published or reinstate)
        repost_stmt = (
            sa_select(Repost, Post, Profile)
            .join(Post, Post.id == Repost.post_id)
            .outerjoin(Profile, Profile.user_id == Post.author_user_id)
            .where(
                Repost.user_id == effective_user_id,
                Post.state.in_(FEED_VISIBLE_POST_STATES),
            )
            .options(selectinload(Post.attachments).selectinload(PostAttachment.media_asset))
            .order_by(Repost.created_at.desc())
        )
        repost_results = (await db.execute(repost_stmt)).all()

        # Get the reposter's own profile for the outer repost item
        reposter_profile_stmt = sa_select(Profile).where(Profile.user_id == effective_user_id)
        reposter_profile = (await db.execute(reposter_profile_stmt)).scalar_one_or_none()

        for repost, post, author_profile in repost_results:
            repost_items.append((repost, post, author_profile, reposter_profile))

    post_ids = [post.id for post, *_ in rows]
    repost_post_ids = [post.id for _, post, *_ in repost_items]
    all_post_ids = post_ids + repost_post_ids

    from apps.engagement.repositories import fetch_post_engagement_flags
    from apps.engagement.services.post_reaction_formatters import load_latest_post_reactions
    from apps.engagement.services.reaction_service import format_user_reaction

    engagement_flags = await fetch_post_engagement_flags(
        db,
        current_user.id,
        all_post_ids,
    )
    latest_reactions = await load_latest_post_reactions(db, all_post_ids, per_type_limit=3)

    from apps.connections.services.recommendation_service import get_user_connections
    from apps.feed.services.profile_enrichment import (
        load_profile_details,
        load_requested_user_ids,
    )

    profiles_by_user_id: dict = {}
    for post, author_profile, *_ in rows:
        if author_profile is not None:
            profiles_by_user_id[post.author_user_id] = author_profile
    for _repost, post, author_profile, reposter_profile in repost_items:
        if author_profile is not None:
            profiles_by_user_id[post.author_user_id] = author_profile
        reposter_user_id = getattr(reposter_profile, "user_id", None)
        if reposter_profile is not None and reposter_user_id is not None:
            profiles_by_user_id[reposter_user_id] = reposter_profile

    connection_ids = await get_user_connections(db, current_user.id)
    profile_details = await load_profile_details(db, profiles_by_user_id)
    requested_user_ids = await load_requested_user_ids(
        db,
        current_user.id,
        set(profiles_by_user_id),
    )

    # Format authored posts (reposted_data is null)
    items = []
    for post, author_profile, mod_user, mod_profile in rows:
        items.append(
            format_post_detail(
                post,
                author_profile=author_profile,
                moderator_user=mod_user,
                moderator_profile=mod_profile,
                is_connected=post.author_user_id in connection_ids,
                is_requested=post.author_user_id in requested_user_ids,
                profile_details=profile_details.get(post.author_user_id),
                is_liked=engagement_flags.user_reaction_for(post.id) is not None,
                is_reposted=post.id in engagement_flags.reposted_post_ids,
                is_bookmarked=post.id in engagement_flags.bookmarked_post_ids,
                user_reaction=format_user_reaction(engagement_flags.user_reaction_for(post.id)),
                reactions=latest_reactions.get(post.id),
                reposted_data=None,
                viewer_user_id=current_user.id,
            )
        )

    # Format reposts: outer = reposter common post; original only in reposted_data
    for repost, post, author_profile, reposter_profile in repost_items:
        # Skip if this post is already in the authored list
        if post.id in post_ids:
            continue
        reposter_user_id = getattr(reposter_profile, "user_id", None)
        items.append(
            format_repost_item(
                post,
                original_author_profile=author_profile,
                reposter_profile=reposter_profile,
                repost_id=repost.id,
                reposted_at=repost.created_at,
                original_author_is_connected=post.author_user_id in connection_ids,
                original_author_is_requested=post.author_user_id in requested_user_ids,
                reposter_is_connected=(
                    reposter_user_id in connection_ids if reposter_user_id else False
                ),
                reposter_is_requested=(
                    reposter_user_id in requested_user_ids if reposter_user_id else False
                ),
                original_author_details=profile_details.get(post.author_user_id),
                reposter_details=(
                    profile_details.get(reposter_user_id) if reposter_user_id else None
                ),
                is_liked=engagement_flags.user_reaction_for(post.id) is not None,
                viewer_has_reposted=post.id in engagement_flags.reposted_post_ids,
                is_bookmarked=post.id in engagement_flags.bookmarked_post_ids,
                user_reaction=format_user_reaction(engagement_flags.user_reaction_for(post.id)),
                reactions=latest_reactions.get(post.id),
                viewer_user_id=current_user.id,
            )
        )

    items.sort(
        key=lambda item: (
            item.get("created_at") or datetime.min.replace(tzinfo=timezone.utc),
            str(item.get("id") or ""),
        ),
        reverse=True,
    )

    total_items = total_items + len([ri for ri in repost_items if ri[1].id not in post_ids])
    return items, total_items


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


def _format_reviewed_post_item(
    post: Post,
    profile,
    moderator_user=None,
    moderator_profile=None,
    *,
    reactions=None,
    report_count: int = 0,
    viewer_user_id: UUID | None = None,
    triggered_moderation_review: bool = False,
) -> dict:
    content = post.content or {}
    is_repostable = (
        True
        if viewer_user_id is None
        else viewer_user_id != post.author_user_id
    )

    return {
        "id": post.id,
        "user_id": post.author_user_id,
        "caption": content.get("caption"),
        "content_html": content.get("content_html") or "",
        "status": post.state.value if hasattr(post.state, "value") else str(post.state),
        "is_moderator_reviewed": post.is_moderator_reviewed,
        "reviewed_at": post.reviewed_at,
        "created_at": post.created_at,
        "updated_at": post.updated_at,
        "is_edited": bool(getattr(post, "is_edited", False)),
        "triggered_moderation_review": bool(triggered_moderation_review),
        "revision_number": post.revision_number,
        "like_count": getattr(post, "like_count", 0) or 0,
        "repost_count": getattr(post, "repost_count", 0) or 0,
        "share_count": getattr(post, "share_count", 0) or 0,
        "comment_count": getattr(post, "comment_count", 0) or 0,
        "report_count": report_count,
        "is_repostable": is_repostable,
        "moderator_id": post.moderator_id,
        "moderator_name": _resolve_moderator_name(moderator_user, moderator_profile),
        "moderation_notes": getattr(post, "moderation_notes", None),
        "profilePhoto_url": (
            generate_profile_image_url(profile.profile_photo_url)
            if profile and profile.profile_photo_url
            else None
        ),
        "first_name": profile.first_name if profile else None,
        "last_name": profile.last_name if profile else None,
        "media": _format_reviewed_post_media(post),
        "reactions": (
            reactions.model_dump()
            if isinstance(reactions, PostReactionsGrouped)
            else reactions
            if reactions is not None
            else PostReactionsGrouped().model_dump()
        ),
    }


def _format_processing_post_item(post: Post, profile, mod_user=None, mod_profile=None) -> dict:
    content = post.content or {}

    return {
        "user_id": post.author_user_id,
        "first_name": profile.first_name if profile else None,
        "last_name": profile.last_name if profile else None,
        "profilePhoto_url": (
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
        "moderator_name": _resolve_moderator_name(mod_user, mod_profile),
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

    await _repair_unassigned_moderators(db, target_state=PostState.processing)

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


async def list_reviewed_posts_by_state_service(
    db: AsyncSession,
    moderator_id: UUID | None,
    status: Literal[
        "published", "flagged", "rejected", "reinstate", "escalate", "processing"
    ]
    | None = None,
    page: int | None = None,
    page_size: int | None = None,
    viewer_user_id: UUID | None = None,
) -> dict:
    """List reviewed posts filtered by ``Post.state``.

    ``Post.state`` drives moderator dashboard tabs. Most ``status`` values map
    1:1 to a post state; ``flagged`` also includes ``processing`` (re-opened
    for review). When omitted, ``status`` defaults to ``published``.
    An optional ``moderator_id`` additionally scopes results to a single moderator.
    """
    from common.pagination import build_paginated_response
    from apps.feed.repositories.post_repository import (
        count_reviewed_posts_for_moderator,
        fetch_reviewed_posts_for_moderator,
        count_reviewed_posts_summary_by_state,
    )
    from apps.feed.repositories.post_revision_repository import (
        posts_with_triggered_moderation_review,
    )

    await _repair_unassigned_moderators_for_state(db, status=status)

    total_items = await count_reviewed_posts_for_moderator(db, moderator_id, status)
    summary = await count_reviewed_posts_summary_by_state(db, moderator_id)

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
    from apps.engagement.services.post_reaction_formatters import load_latest_post_reactions
    from apps.report.repositories.report_repository import count_reports_by_entity_ids
    from common.enums import ReportEntityType

    post_ids = [post.id for post, *_ in posts]
    latest_reactions = await load_latest_post_reactions(db, post_ids, per_type_limit=3)
    report_counts = await count_reports_by_entity_ids(
        db,
        ReportEntityType.post,
        post_ids,
    )
    triggered_ids = await posts_with_triggered_moderation_review(db, post_ids)
    formatted = [
        _format_reviewed_post_item(
            post,
            profile,
            mod_user,
            mod_profile,
            reactions=latest_reactions.get(post.id),
            report_count=report_counts.get(post.id, 0),
            viewer_user_id=viewer_user_id,
            triggered_moderation_review=post.id in triggered_ids,
        )
        for post, profile, mod_user, mod_profile in posts
    ]
    res = build_paginated_response(formatted, p, ps, total_items).model_dump()
    res["summary"] = summary
    return res
