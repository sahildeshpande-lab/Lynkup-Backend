from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from apps.accounts.db_models import User, UserInstallation
from apps.accounts.services.common_service import _generate_otp, _now
from common.enums import UserStatus
from core.email_service import send_otp_email


def clear_session_email_verification(user: User) -> None:
    user.email_verified_at = None
    user.email_otp = None
    user.email_otp_created_at = None


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
    now = _now()
    otp = _generate_otp()
    await db.refresh(user)
    clear_session_email_verification(user)
    user.email_otp = otp
    user.email_otp_created_at = now
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
