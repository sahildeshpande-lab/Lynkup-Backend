"""Automatic blacklist scanning for posts and comments.

Preserves the existing moderation lifecycle: on match, posts are set to
``PostState.flagged`` with action ``"flagged"``; comments use ``is_deleted``.
Moderator assignment reuses ``assign_next_moderator_round_robin``.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.engagement.db_models import Comment
from apps.engagement.repositories.comment_repository import (
    mark_comment_deleted,
    update_post_comment_count,
)
from apps.feed.content_utils import normalize_text
from apps.feed.db_models import Post
from apps.moderation.config import settings as auto_moderation_settings
from apps.moderation.db_models import ModerationWordsConfig
from apps.moderation.services.moderation_history_service import record_moderation_history
from apps.moderation.services.moderator_assignment_service import (
    assign_next_moderator_round_robin,
)
from common.enums import PostState, ReportEntityType
from common.exceptions import ApiError
from core.jobs.claims import (
    ClaimResult,
    claim_moderation_comment,
    claim_moderation_post,
)

logger = logging.getLogger(__name__)

# Terminal / not-yet-submitted states are excluded from automatic scanning.
_SKIP_POST_STATES = frozenset({PostState.draft, PostState.deleted, PostState.rejected})

# Matches existing ``_MODERATION_STATES`` in post_service: RR assignment for
# clean posts only when they are awaiting / in the published review path.
_ASSIGN_ON_CLEAN_STATES = frozenset({PostState.processing, PostState.published})

_COUNTED_POST_STATES = frozenset({PostState.published, PostState.reinstate})


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def find_matching_words(content: str, blacklist: list[str]) -> list[str]:
    """Return unique blacklist keywords found as case-insensitive whole words.

    Keywords are matched as literal text (``re.escape``), bounded by non-word
    characters so ``spam`` hits ``spam!`` but not ``spammer`` or ``antispam``.
    """
    if not content or not blacklist:
        return []

    haystack = content.casefold()
    found: list[str] = []
    seen: set[str] = set()
    for raw in blacklist:
        if not raw:
            continue
        keyword = str(raw).strip()
        if not keyword:
            continue
        folded = keyword.casefold()
        if folded in seen:
            continue
        pattern = rf"(?<!\w){re.escape(folded)}(?!\w)"
        if re.search(pattern, haystack):
            seen.add(folded)
            found.append(folded)
    return found


def _auto_flag_note(blacklist_matches: list[str]) -> str:
    return (
        "Automatically flagged due to blacklist keyword match: "
        + ", ".join(blacklist_matches)
    )


def extract_post_scan_text(post: Post) -> str:
    """Build plain text from post caption and HTML body for keyword matching."""
    parts: list[str] = []
    caption = post.caption
    if caption:
        parts.append(str(caption))
    content_html = post.content_html
    if content_html:
        parts.append(normalize_text(str(content_html)))
    return " ".join(parts).strip()


async def _load_blacklist(db: AsyncSession) -> list[str]:
    result = await db.execute(select(ModerationWordsConfig).limit(1))
    config = result.scalar_one_or_none()
    if config is None:
        return []
    words = list(config.profanity_words or [])
    return [str(w).strip().casefold() for w in words if w and str(w).strip()]


async def _ensure_moderator_assigned(post: Post, db: AsyncSession) -> None:
    """Assign a moderator via existing RR when ``moderator_id`` is unset."""
    if post.moderator_id is not None:
        return
    post.moderator_id = await assign_next_moderator_round_robin(db)
    await db.flush()
    if post.moderator_id is not None:
        try:
            from apps.notifications.services import notify_post_assigned

            await notify_post_assigned(
                db,
                post_id=post.id,
                author_user_id=post.author_user_id,
                moderator_id=post.moderator_id,
            )
        except Exception:
            logger.exception(
                "Failed to dispatch post assigned notification post_id=%s moderator_id=%s",
                post.id,
                post.moderator_id,
            )


def _needs_scan(scanned_at: datetime | None, updated_at: datetime) -> bool:
    if scanned_at is None:
        return True
    scanned = scanned_at
    updated = updated_at
    if scanned.tzinfo is None:
        scanned = scanned.replace(tzinfo=timezone.utc)
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return updated > scanned


async def _fetch_posts_needing_scan(
    db: AsyncSession,
    *,
    limit: int,
) -> list[Post]:
    stmt = (
        select(Post)
        .where(
            Post.state.notin_(_SKIP_POST_STATES),
            or_(
                Post.auto_moderation_scanned_at.is_(None),
                Post.updated_at > Post.auto_moderation_scanned_at,
            ),
        )
        .order_by(Post.updated_at.asc(), Post.created_at.asc(), Post.id.asc())
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


async def _fetch_comments_needing_scan(
    db: AsyncSession,
    *,
    limit: int,
) -> list[Comment]:
    stmt = (
        select(Comment)
        .where(
            Comment.is_deleted.is_(False),
            or_(
                Comment.auto_moderation_scanned_at.is_(None),
                Comment.updated_at > Comment.auto_moderation_scanned_at,
            ),
        )
        .order_by(Comment.updated_at.asc(), Comment.created_at.asc(), Comment.id.asc())
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


async def _process_post(
    db: AsyncSession,
    post: Post,
    blacklist: list[str],
) -> None:
    if not _needs_scan(post.auto_moderation_scanned_at, post.updated_at):
        return
    if post.state in _SKIP_POST_STATES:
        return

    now = utc_now()
    text = extract_post_scan_text(post)
    matches = find_matching_words(text, blacklist)
    previous_state = post.state

    if matches:
        was_counted = previous_state in _COUNTED_POST_STATES
        transitioning_to_flagged = previous_state != PostState.flagged

        post.state = PostState.flagged
        post.moderation_words_found = matches
        note = _auto_flag_note(matches)
        post.moderation_notes = note
        post.updated_at = now
        post.auto_moderation_scanned_at = now

        await _ensure_moderator_assigned(post, db)

        if transitioning_to_flagged:
            await record_moderation_history(
                db,
                entity_type=ReportEntityType.post,
                entity_id=post.id,
                action="flagged",
                moderator_id=post.moderator_id,
                comment=note,
            )

        db.add(post)
        await db.commit()

        if transitioning_to_flagged and was_counted:
            try:
                from apps.profiles.services.profile_stats_service import (
                    sync_posts_count_for_visibility_change,
                )

                await sync_posts_count_for_visibility_change(
                    db,
                    post_id=post.id,
                    author_user_id=post.author_user_id,
                    was_counted=True,
                    now_counted=False,
                )
                await db.commit()
            except Exception:
                logger.exception(
                    "Failed to update posts_count after auto-flagging post %s",
                    post.id,
                )
        return

    # No match — preserve existing state; never auto-reinstate.
    post.moderation_words_found = []
    post.auto_moderation_scanned_at = now

    if post.state in _ASSIGN_ON_CLEAN_STATES:
        await _ensure_moderator_assigned(post, db)

    db.add(post)
    await db.commit()


async def _process_comment(
    db: AsyncSession,
    comment: Comment,
    blacklist: list[str],
) -> None:
    if comment.is_deleted:
        return
    if not _needs_scan(comment.auto_moderation_scanned_at, comment.updated_at):
        return

    now = utc_now()
    matches = find_matching_words(comment.comment_text or "", blacklist)

    if matches:
        if comment.parent_comment_id is None:
            await update_post_comment_count(db, comment.post_id, -1)
        await mark_comment_deleted(db, comment, now=now)
        comment.moderation_words_found = matches
        comment.auto_moderation_scanned_at = now
        db.add(comment)
        await db.commit()
        return

    comment.moderation_words_found = []
    comment.auto_moderation_scanned_at = now
    db.add(comment)
    await db.commit()


def _resolve_session_factory(session_factory=None) -> async_sessionmaker[AsyncSession]:
    if session_factory is not None:
        return session_factory
    from core.database.session import async_session_factory

    return async_session_factory


def _resolve_lease_owner(lease_owner: str | None) -> str:
    return lease_owner or f"auto-moderation:{uuid4()}"


async def _clear_moderation_post_lease_if_owner(
    db: AsyncSession,
    *,
    post_id: UUID,
    lease_owner: str,
) -> bool:
    """Clear the post lease only while this worker still owns it."""
    result = await db.execute(
        update(Post)
        .where(Post.id == post_id)
        .where(Post.moderation_lease_owner == lease_owner)
        .values(
            moderation_lease_owner=None,
            moderation_lease_expires_at=None,
        )
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


async def _clear_moderation_comment_lease_if_owner(
    db: AsyncSession,
    *,
    comment_id: UUID,
    lease_owner: str,
) -> bool:
    """Clear the comment lease only while this worker still owns it."""
    result = await db.execute(
        update(Comment)
        .where(Comment.id == comment_id)
        .where(Comment.moderation_lease_owner == lease_owner)
        .values(
            moderation_lease_owner=None,
            moderation_lease_expires_at=None,
        )
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


async def _scan_post_ids(
    post_ids: list[UUID],
    *,
    session_factory: async_sessionmaker[AsyncSession],
    lease_owner: str,
) -> int:
    processed = 0
    for post_id in post_ids:
        async with session_factory() as claim_session:
            claim: ClaimResult = await claim_moderation_post(
                claim_session,
                post_id=post_id,
                lease_owner=lease_owner,
            )
        if not claim.claimed:
            continue
        try:
            async with session_factory() as work_session:
                result = await work_session.execute(select(Post).where(Post.id == post_id))
                fresh = result.scalar_one_or_none()
                if fresh is None:
                    continue
                words = await _load_blacklist(work_session)
                await _process_post(work_session, fresh, words)
        except ApiError:
            logger.exception(
                "Auto-moderation skipped post %s due to assignment/API error",
                post_id,
            )
            continue
        except Exception:
            logger.exception("Auto-moderation failed for post %s", post_id)
            continue
        async with session_factory() as complete_session:
            await _clear_moderation_post_lease_if_owner(
                complete_session,
                post_id=post_id,
                lease_owner=lease_owner,
            )
            await complete_session.commit()
        processed += 1
    return processed


async def _scan_comment_ids(
    comment_ids: list[UUID],
    *,
    session_factory: async_sessionmaker[AsyncSession],
    lease_owner: str,
) -> int:
    processed = 0
    for comment_id in comment_ids:
        async with session_factory() as claim_session:
            claim: ClaimResult = await claim_moderation_comment(
                claim_session,
                comment_id=comment_id,
                lease_owner=lease_owner,
            )
        if not claim.claimed:
            continue
        try:
            async with session_factory() as work_session:
                result = await work_session.execute(
                    select(Comment).where(Comment.id == comment_id)
                )
                fresh = result.scalar_one_or_none()
                if fresh is None:
                    continue
                words = await _load_blacklist(work_session)
                await _process_comment(work_session, fresh, words)
        except Exception:
            logger.exception("Auto-moderation failed for comment %s", comment_id)
            continue
        async with session_factory() as complete_session:
            await _clear_moderation_comment_lease_if_owner(
                complete_session,
                comment_id=comment_id,
                lease_owner=lease_owner,
            )
            await complete_session.commit()
        processed += 1
    return processed


async def scan_posts(
    db: AsyncSession,
    *,
    batch_size: int | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    lease_owner: str | None = None,
) -> int:
    """Scan up to ``batch_size`` posts that need auto-moderation. Returns processed count."""
    limit = batch_size if batch_size is not None else auto_moderation_settings.batch_size
    posts = await _fetch_posts_needing_scan(db, limit=limit)
    return await _scan_post_ids(
        [post.id for post in posts],
        session_factory=_resolve_session_factory(session_factory),
        lease_owner=_resolve_lease_owner(lease_owner),
    )


async def scan_comments(
    db: AsyncSession,
    *,
    batch_size: int | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    lease_owner: str | None = None,
) -> int:
    """Scan up to ``batch_size`` comments that need auto-moderation. Returns processed count."""
    limit = batch_size if batch_size is not None else auto_moderation_settings.batch_size
    comments = await _fetch_comments_needing_scan(db, limit=limit)
    return await _scan_comment_ids(
        [comment.id for comment in comments],
        session_factory=_resolve_session_factory(session_factory),
        lease_owner=_resolve_lease_owner(lease_owner),
    )


async def run_auto_moderation_scan(
    *,
    batch_size: int | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    lease_owner: str | None = None,
) -> dict[str, int]:
    """Run one scan cycle for posts and comments."""
    factory = _resolve_session_factory(session_factory)
    owner = _resolve_lease_owner(lease_owner)
    limit = batch_size if batch_size is not None else auto_moderation_settings.batch_size
    async with factory() as session:
        post_ids = [post.id for post in await _fetch_posts_needing_scan(session, limit=limit)]
    posts_processed = await _scan_post_ids(
        post_ids,
        session_factory=factory,
        lease_owner=owner,
    )
    async with factory() as session:
        comment_ids = [
            comment.id
            for comment in await _fetch_comments_needing_scan(session, limit=limit)
        ]
    comments_processed = await _scan_comment_ids(
        comment_ids,
        session_factory=factory,
        lease_owner=owner,
    )
    logger.info(
        "Auto-moderation scan finished: posts=%d comments=%d batch_size=%d",
        posts_processed,
        comments_processed,
        limit,
    )
    return {"posts": posts_processed, "comments": comments_processed}
