from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from apps.user_deletion.config import settings as deletion_settings
from common.enums import UserStatus

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def apply_scheduled_deletion_fields(
    user: User,
    *,
    now: datetime | None = None,
    purge_after_days: int | None = None,
) -> None:
    """Set soft-delete / grace-period fields on a user (DB row not committed)."""
    timestamp = now or _now()
    days = (
        deletion_settings.account_purge_after_days
        if purge_after_days is None
        else purge_after_days
    )
    user.status = UserStatus.deleting
    user.is_deleted = True
    user.deleted_at = timestamp
    user.purge_after = timestamp + timedelta(days=days)


_STAFF_ROLES = frozenset({"moderator", "viewer", "superadmin"})


def _is_staff_user(user: User) -> bool:
    role = getattr(user, "role", None) or "user"
    return role in _STAFF_ROLES


async def restore_deleting_account_if_eligible(
    user: User,
    db: AsyncSession,
    *,
    now: datetime | None = None,
) -> bool:
    """Restore a grace-period *app user* on login/social auth.

    Moderators, viewers, and superadmins stay locked out for the rest of the
    deleting window — they cannot recover by logging in.

    Returns True when the account was restored, False when not eligible.
    Does not commit — caller owns the transaction.
    """
    timestamp = now or _now()
    if _is_staff_user(user):
        return False
    if user.status != UserStatus.deleting:
        return False
    if user.purge_after is None:
        return False

    purge_after = user.purge_after
    if purge_after.tzinfo is None:
        purge_after = purge_after.replace(tzinfo=timezone.utc)

    if purge_after <= timestamp:
        return False

    user.status = UserStatus.active
    user.is_deleted = False
    user.deleted_at = None
    user.purge_after = None
    user.updated_at = timestamp
    db.add(user)

    logger.info(
        "[account-deletion] Restored deleting account user_id=%s",
        user.id,
    )
    return True


async def run_deletion_request_side_effects(user: User, db: AsyncSession) -> None:
    """Best-effort access side effects after scheduled deletion is committed.

    Grace period must NOT permanently delete Firebase/Stream identity or mutate
    PostgreSQL content. Only revoke sessions / deactivate Stream access so the
    owner cannot keep using the product, while login recovery remains possible.
    """
    from apps.accounts.services.device_otp_service import (
        deactivate_push_for_user_installations,
    )
    from apps.chat.service import deactivate_stream_user_best_effort
    from core.auth.services import revoke_firebase_tokens

    try:
        await deactivate_push_for_user_installations(db, user.id)
        await db.commit()
    except Exception:
        logger.exception(
            "[account-deletion] Failed clearing push installations user_id=%s",
            user.id,
        )
        try:
            await db.rollback()
        except Exception:  # nosec B110 -- best-effort rollback cleanup
            pass

    # Revoke refresh tokens only — do NOT disable/delete the Firebase user, or
    # password/social re-auth for grace-period recovery becomes impossible.
    if user.firebase_uid and not str(user.firebase_uid).startswith("admin-"):
        try:
            revoke_firebase_tokens(user.firebase_uid)
        except Exception:
            logger.exception(
                "[account-deletion] Failed to revoke Firebase tokens uid=%s",
                user.firebase_uid,
            )

    await deactivate_stream_user_best_effort(user)


async def run_recovery_side_effects(user: User, db: AsyncSession) -> None:
    """Best-effort restore of Stream after DB recovery.

    Firebase identity was never disabled during the grace period — the client
    re-authenticates normally. Stream access is reactivated here.
    """
    from apps.chat.service import reactivate_stream_user_best_effort

    await reactivate_stream_user_best_effort(user, db)


def is_purge_window_expired(user: User, *, now: datetime | None = None) -> bool:
    timestamp = now or _now()
    if user.purge_after is None:
        return True
    purge_after = user.purge_after
    if purge_after.tzinfo is None:
        purge_after = purge_after.replace(tzinfo=timezone.utc)
    return purge_after <= timestamp


async def remove_expired_deleting_user_for_resignup(
    db: AsyncSession,
    user_id: UUID,
) -> None:
    """Hard-delete a grace-expired user so their email can be used for a new signup."""
    from sqlalchemy import text

    await db.execute(
        text("CALL purge_user_data(:user_id)"),
        {"user_id": str(user_id)},
    )
    await db.flush()
