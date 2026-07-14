from __future__ import annotations

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


async def evaluate_device_otp_requirement(
    db: AsyncSession,
    user: User,
    device_id: str,
) -> tuple[UserInstallation | None, bool, bool]:
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

    if is_new_device:
        db.add(
            UserInstallation(
                user_id=user.id,
                device_id=device_id,
                platform=None,
                app_version=None,
                installed_at=now,
                last_active_at=now,
                is_active=True,
            )
        )
    elif installation is not None:
        installation.last_active_at = now
        installation.is_active = True
        db.add(installation)

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
