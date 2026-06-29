from __future__ import annotations
import logging
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from apps.accounts.db_models import RefreshToken, User, UserInstallation
from common.enums import UserStatus
from ..schemas import LogoutRequest, LogoutRequest
from core.auth.services import revoke_firebase_tokens
logger = logging.getLogger(__name__)

from .common_service import _now, _revoke_refresh_token_row

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

    if firebase_uid:
        try:
            revoke_firebase_tokens(firebase_uid)
        except Exception as exc:
            logger.exception(
                "Firebase token revocation failed during logout for user %s",
                user.id,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to revoke Firebase session",
            ) from exc

    await db.commit()

async def logout_all(current_user: User, db: AsyncSession) -> dict:
    rows = (await db.execute(select(RefreshToken).where(RefreshToken.user_id == current_user.id, RefreshToken.revoked_at == None))).scalars().all()
    for row in rows:
        await _revoke_refresh_token_row(db, row)

    # current_user.status = UserStatus.pending
    current_user.updated_at = _now()
    db.add(current_user)
    await db.commit()
    return {"logged_out_all": True}
