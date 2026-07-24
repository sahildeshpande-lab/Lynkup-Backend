from __future__ import annotations
import logging
from uuid import UUID
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from apps.accounts.db_models import RefreshToken, User, UserInstallation
from common.enums import UserStatus
from ..schemas import LogoutRequest, LogoutRequest
from core.auth.services import revoke_firebase_tokens
logger = logging.getLogger(__name__)

from .common_service import _now, _revoke_refresh_token_row
from .device_otp_service import clear_session_email_verification

async def logout(
    payload: LogoutRequest,
    firebase_user: dict,
    db: AsyncSession,
) -> None:
    firebase_uid = firebase_user.get("uid")

    if not firebase_uid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Firebase user",
        )

    user = (
        await db.execute(
            select(User).where(User.firebase_uid == firebase_uid)
        )
    ).scalar_one_or_none()
    if user is None:
        try:
            user_id = UUID(str(firebase_uid))
        except ValueError:
            user_id = None
        if user_id is not None:
            user = (
                await db.execute(
                    select(User).where(User.id == user_id)
                )
            ).scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    device_id = payload.device_id.strip()

    installation = (
        await db.execute(
            select(UserInstallation).where(
                UserInstallation.user_id == user.id,
                UserInstallation.device_id == device_id,
            )
        )
    ).scalar_one_or_none()

    if installation:
        # Keep the installation row so this device stays trusted after logout.
        # OTP is only required when signing in from a *new* device.
        installation.is_active = False
        installation.last_active_at = _now()
        db.add(installation)
    else:
        logger.info(
            "Logout requested for user %s with unknown device_id %s",
            user.id,
            device_id,
        )

    token_rows = (
        await db.execute(
            select(RefreshToken).where(
                RefreshToken.user_id == user.id,
                RefreshToken.device_id == device_id,
                RefreshToken.revoked_at.is_(None),
            )
        )
    ).scalars().all()

    for token_row in token_rows:
        await _revoke_refresh_token_row(db, token_row)

    # Clear any pending OTP codes, but keep email_verified_at so returning to
    # this known device does not require OTP again.
    user.email_otp = None
    user.email_otp_created_at = None
    user.updated_at = _now()
    db.add(user)

    if firebase_uid:
        try:
            revoke_firebase_tokens(firebase_uid)
        except Exception as exc:
            logger.warning(
                "Firebase token revocation failed during logout for user %s",
                user.id,
                exc_info=True,
            )

    await db.commit()

async def logout_all(current_user: User, db: AsyncSession) -> dict:
    rows = (await db.execute(select(RefreshToken).where(RefreshToken.user_id == current_user.id, RefreshToken.revoked_at == None))).scalars().all()
    for row in rows:
        await _revoke_refresh_token_row(db, row)

    installation_rows = (
        await db.execute(select(UserInstallation).where(UserInstallation.user_id == current_user.id))
    ).scalars().all()
    for installation in installation_rows:
        await db.delete(installation)

    # Logout-all removes every trusted device, so the next sign-in requires OTP.
    clear_session_email_verification(current_user)
    current_user.updated_at = _now()
    db.add(current_user)
    await db.commit()
    return {"logged_out_all": True}
