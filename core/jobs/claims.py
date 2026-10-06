from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from uuid import UUID

from sqlalchemy import func, or_, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import TransactionalEmailLog
from apps.engagement.db_models import Comment
from apps.feed.db_models import Post
from common.enums import PostState

# Must match apps.moderation.services.auto_moderation_service._SKIP_POST_STATES.
_SKIP_POST_STATES = frozenset({PostState.draft, PostState.deleted, PostState.rejected})


@dataclass(frozen=True)
class ClaimResult:
    claimed: bool
    entity_id: Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def claim_transactional_email(
    db: AsyncSession,
    *,
    email_id: Any,
    lease_owner: str,
    lease_seconds: int = 300,
) -> ClaimResult:
    """Atomically claim one transactional email for processing.

    An email is eligible when:
      - is_sent = false
      - and either there is no active lease
        or the existing lease has expired.
    """
    db_now = func.now()
    lease_expires_at = utc_now() + timedelta(seconds=int(lease_seconds))
    stmt = (
        update(TransactionalEmailLog)
        .where(TransactionalEmailLog.id == email_id)
        .where(TransactionalEmailLog.is_send.is_(False))
        .where(
            or_(
                TransactionalEmailLog.lease_owner.is_(None),
                TransactionalEmailLog.lease_expires_at.is_(None),
                TransactionalEmailLog.lease_expires_at < db_now,
            )
        )
        .values(
            lease_owner=lease_owner,
            lease_expires_at=lease_expires_at,
            attempt_count=TransactionalEmailLog.attempt_count + 1,
            updated_at=db_now,
        )
        .execution_options(synchronize_session=False)
    )
    result = await db.execute(stmt)
    claimed = result.rowcount == 1
    if claimed:
        await db.commit()
    return ClaimResult(claimed=claimed, entity_id=email_id)


def _claim_succeeded(result) -> bool:
    scalar = getattr(result, "scalar_one_or_none", None)
    if callable(scalar):
        value = scalar()
        if value is not None:
            return True
    return int(getattr(result, "rowcount", 0) or 0) == 1


def _moderation_lease_expired(owner_column, expires_column, db_now):
    return or_(
        owner_column.is_(None),
        expires_column.is_(None),
        expires_column < db_now,
    )


def _needs_auto_moderation_scan(scanned_at_column, updated_at_column):
    return or_(
        scanned_at_column.is_(None),
        updated_at_column > scanned_at_column,
    )


async def claim_moderation_post(
    db: AsyncSession,
    *,
    post_id: UUID,
    lease_owner: str,
    lease_seconds: int = 300,
) -> ClaimResult:
    """Atomically claim one post for auto-moderation.

    A post is eligible when:
      - state is not draft/deleted/rejected
      - it still needs scanning (auto_moderation_scanned_at is null or stale)
      - and either there is no active moderation lease or it has expired.
    """
    db_now = func.current_timestamp()
    stmt = (
        update(Post)
        .where(Post.id == post_id)
        .where(Post.state.notin_(_SKIP_POST_STATES))
        .where(_needs_auto_moderation_scan(Post.auto_moderation_scanned_at, Post.updated_at))
        .where(
            _moderation_lease_expired(
                Post.moderation_lease_owner,
                Post.moderation_lease_expires_at,
                db_now,
            )
        )
        .values(
            moderation_lease_owner=lease_owner,
            moderation_lease_expires_at=db_now
            + text(f"INTERVAL '{int(lease_seconds)} seconds'"),
            moderation_attempt_count=Post.moderation_attempt_count + 1,
        )
        .returning(Post.id)
        .execution_options(synchronize_session=False)
    )
    result = await db.execute(stmt)
    claimed = _claim_succeeded(result)
    if claimed:
        await db.commit()
    return ClaimResult(claimed=claimed, entity_id=post_id)


async def claim_moderation_comment(
    db: AsyncSession,
    *,
    comment_id: UUID,
    lease_owner: str,
    lease_seconds: int = 300,
) -> ClaimResult:
    """Atomically claim one comment for auto-moderation.

    A comment is eligible when:
      - is_deleted = false
      - it still needs scanning (auto_moderation_scanned_at is null or stale)
      - and either there is no active moderation lease or it has expired.
    """
    db_now = func.current_timestamp()
    stmt = (
        update(Comment)
        .where(Comment.id == comment_id)
        .where(Comment.is_deleted.is_(False))
        .where(
            _needs_auto_moderation_scan(
                Comment.auto_moderation_scanned_at,
                Comment.updated_at,
            )
        )
        .where(
            _moderation_lease_expired(
                Comment.moderation_lease_owner,
                Comment.moderation_lease_expires_at,
                db_now,
            )
        )
        .values(
            moderation_lease_owner=lease_owner,
            moderation_lease_expires_at=db_now
            + text(f"INTERVAL '{int(lease_seconds)} seconds'"),
            moderation_attempt_count=Comment.moderation_attempt_count + 1,
        )
        .returning(Comment.id)
        .execution_options(synchronize_session=False)
    )
    result = await db.execute(stmt)
    claimed = _claim_succeeded(result)
    if claimed:
        await db.commit()
    return ClaimResult(claimed=claimed, entity_id=comment_id)
