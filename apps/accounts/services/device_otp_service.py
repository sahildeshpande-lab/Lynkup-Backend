from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from apps.accounts.db_models import User, UserInstallation
from apps.accounts.services.common_service import _generate_otp, _now
from common.enums import UserStatus
from core.email_service import send_otp_email

logger = logging.getLogger(__name__)


def clear_session_email_verification(user: User) -> None:
    user.email_verified_at = None
    user.email_otp = None
    user.email_otp_created_at = None


async def release_fcm_token_from_other_installations(
    db: AsyncSession,
    fcm_token: str,
    *,
    keep_user_id: UUID,
) -> int:
    """
    Detach a device FCM token from every other user's installation rows.

    One physical device should only receive pushes for the currently signed-in
    account. Clears the token, deactivates stale rows, and unsubscribes the
    device from the previous user's Firebase topics (announcements, topic
    campaigns, and direct token pushes all rely on active installation tokens).
    """
    token = (fcm_token or "").strip()
    if not token:
        return 0

    stmt = select(UserInstallation).where(
        UserInstallation.fcm_token == token,
        UserInstallation.user_id != keep_user_id,
    )
    stale_installations = list((await db.execute(stmt)).scalars().all())
    if not stale_installations:
        return 0

    from apps.notifications.services.topic_service import TopicService

    for installation in stale_installations:
        try:
            await TopicService.unsubscribe_device_from_user_topics(
                db,
                installation.user_id,
                fcm_token=token,
            )
        except Exception:
            logger.exception(
                "Topic unsubscribe failed while releasing FCM token user_id=%s",
                installation.user_id,
            )
        installation.fcm_token = None
        installation.is_active = False
        db.add(installation)

    await db.flush()
    logger.info(
        "Released FCM token from %s other installation(s) for user_id=%s",
        len(stale_installations),
        keep_user_id,
    )
    return len(stale_installations)


async def deactivate_push_for_user_installations(
    db: AsyncSession,
    user_id: UUID,
    *,
    device_id: str | None = None,
) -> int:
    """
    Clear FCM tokens and unsubscribe topics for a user's installation row(s).

    When ``device_id`` is provided but no row matches, falls back to every
    active installation for the user that still has an FCM token so logout still
    stops pushes if the client sends a stale device id.
    """
    from apps.notifications.services.topic_service import TopicService

    stmt = select(UserInstallation).where(UserInstallation.user_id == user_id)
    if device_id:
        stmt = stmt.where(UserInstallation.device_id == device_id)
    installations = list((await db.execute(stmt)).scalars().all())

    if not installations and device_id:
        installations = list(
            (
                await db.execute(
                    select(UserInstallation).where(
                        UserInstallation.user_id == user_id,
                        UserInstallation.is_active.is_(True),
                        UserInstallation.fcm_token.is_not(None),
                    )
                )
            ).scalars().all()
        )
        if installations:
            logger.info(
                "Logout device_id=%s not found for user_id=%s; clearing push on %s active installation(s)",
                device_id,
                user_id,
                len(installations),
            )

    cleared = 0
    for installation in installations:
        fcm_token = (installation.fcm_token or "").strip()
        if fcm_token:
            try:
                await TopicService.unsubscribe_device_from_user_topics(
                    db,
                    user_id,
                    fcm_token=fcm_token,
                )
            except Exception:
                logger.exception(
                    "Topic unsubscribe failed during logout user_id=%s",
                    user_id,
                )
        if fcm_token or installation.is_active:
            installation.fcm_token = None
            installation.is_active = False
            installation.last_active_at = _now()
            db.add(installation)
            cleared += 1

    if cleared:
        await db.flush()
    return cleared


async def get_user_installation(
    db: AsyncSession,
    user_id,
    device_id: str,
) -> UserInstallation | None:
    stmt = select(UserInstallation).where(
        UserInstallation.user_id == user_id,
        UserInstallation.device_id == device_id,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def upsert_user_installation(
    db: AsyncSession,
    user_id,
    device_id: str,
    *,
    platform: str | None = None,
    fcm_token: str | None = None,
    now: datetime | None = None,
) -> UserInstallation:
    """Create or refresh a user_installation row.

    ``fcm_token`` / ``platform`` are optional. When omitted or blank, existing
    values are left unchanged (never cleared to NULL).
    """
    timestamp = now or _now()
    normalized_platform = (platform or "").strip() or None
    normalized_fcm_token = (fcm_token or "").strip() or None

    if normalized_fcm_token is not None:
        await release_fcm_token_from_other_installations(
            db,
            normalized_fcm_token,
            keep_user_id=user_id,
        )

    installation = await get_user_installation(db, user_id, device_id)
    if installation is None:
        installation = UserInstallation(
            user_id=user_id,
            device_id=device_id,
            platform=normalized_platform,
            fcm_token=normalized_fcm_token,
            app_version=None,
            installed_at=timestamp,
            last_active_at=timestamp,
            is_active=True,
        )
        db.add(installation)
        return installation

    installation.last_active_at = timestamp
    installation.is_active = True
    if normalized_platform is not None:
        installation.platform = normalized_platform
    if normalized_fcm_token is not None:
        installation.fcm_token = normalized_fcm_token
    db.add(installation)
    return installation


async def evaluate_device_otp_requirement(
    db: AsyncSession,
    user: User,
    device_id: str,
) -> tuple[UserInstallation | None, bool, bool]:
    """Decide whether this sign-in needs an OTP challenge.

    OTP is required when:
    - the device has never been seen (no ``UserInstallation`` row), or
    - the account email has never been verified (``email_verified_at`` is null).

    A known device that was only deactivated by logout still counts as trusted,
    so returning to it after manual logout does **not** require OTP again.
    """
    installation = await get_user_installation(db, user.id, device_id)
    is_new_device = installation is None
    needs_otp = user.email_verified_at is None or is_new_device
    return installation, is_new_device, needs_otp


async def send_otp_challenge(
    db: AsyncSession,
    user: User,
    device_id: str,
    *,
    installation: UserInstallation | None,
    is_new_device: bool,
    platform: str | None = None,
    fcm_token: str | None = None,
) -> str:
    """Issue an OTP challenge for email or new-device verification.

    Account status becomes ``pending`` only when the user is not already
    ``active``. Once active, status is never downgraded by OTP challenges
    (e.g. signing in on a new device).
    """
    now = _now()
    otp = _generate_otp()
    await db.refresh(user)

    already_active = user.status == UserStatus.active

    user.email_otp = otp
    user.email_otp_created_at = now
    # Never downgrade an active account back to pending.
    if not already_active:
        user.email_verified_at = None
        user.status = UserStatus.pending
    user.updated_at = now
    db.add(user)

    await upsert_user_installation(
        db,
        user.id,
        device_id,
        platform=platform,
        fcm_token=fcm_token,
        now=now,
    )

    await db.commit()
    await send_otp_email(user.email, otp, "email_verification")
    return otp


def attach_otp_flags(
    session_data: dict,
    *,
    email_sent: bool,
    needs_otp: bool,
) -> dict:
    session_data["emailSent"] = email_sent
    session_data["needsOtp"] = needs_otp
    return session_data
