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


async def restore_deleting_account_if_eligible(
    user: User,
    db: AsyncSession,
    *,
    now: datetime | None = None,
) -> bool:
    """Restore a grace-period account on login/social auth.

    Returns True when the account was restored, False when not eligible.
    Does not commit — caller owns the transaction.
    """
    timestamp = now or _now()
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
    """Best-effort external side effects after soft-delete is committed."""
    from apps.accounts.services.device_otp_service import (
        deactivate_push_for_user_installations,
    )
    from apps.chat.service import deactivate_stream_user_best_effort
    from core.auth.services import disable_firebase_user, revoke_firebase_tokens

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
        except Exception:
            pass

    if user.firebase_uid and not str(user.firebase_uid).startswith("admin-"):
        try:
            disable_firebase_user(user.firebase_uid)
        except Exception:
            logger.exception(
                "[account-deletion] Failed to disable Firebase uid=%s",
                user.firebase_uid,
            )
            try:
                revoke_firebase_tokens(user.firebase_uid)
            except Exception:
                logger.exception(
                    "[account-deletion] Failed to revoke Firebase tokens uid=%s",
                    user.firebase_uid,
                )

    await deactivate_stream_user_best_effort(user)


async def run_recovery_side_effects(user: User, db: AsyncSession) -> None:
    """Best-effort restore of Firebase + Stream after DB recovery."""
    from apps.chat.service import reactivate_stream_user_best_effort
    from core.auth.services import enable_firebase_user

    if user.firebase_uid and not str(user.firebase_uid).startswith("admin-"):
        try:
            enable_firebase_user(user.firebase_uid)
        except Exception:
            logger.exception(
                "[account-deletion] Failed to enable Firebase uid=%s",
                user.firebase_uid,
            )

    await reactivate_stream_user_best_effort(user, db)


def is_purge_window_expired(user: User, *, now: datetime | None = None) -> bool:
    timestamp = now or _now()
    if user.purge_after is None:
        return True
    purge_after = user.purge_after
    if purge_after.tzinfo is None:
        purge_after = purge_after.replace(tzinfo=timezone.utc)
    return purge_after <= timestamp
