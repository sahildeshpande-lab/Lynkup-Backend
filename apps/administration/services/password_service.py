from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from pwdlib import PasswordHash
from pwdlib.hashers.bcrypt import BcryptHasher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from apps.accounts.db_models import User
from apps.accounts.schemas import ApiResponse

from ..schemas import AdminForgotPasswordRequest, AdminResetPasswordRequest, ChangePasswordRequest

logger = logging.getLogger(__name__)

PASSWORD_HASHER = PasswordHash((BcryptHasher(),))


def _sync_firebase_password_best_effort(user: User, new_password: str) -> None:
    """Best-effort Firebase password sync during token-based password reset.

    Local ``password_hash`` is always updated from the reset token. Firebase sync
    must not block reset when the UID is missing or stale.
    """
    if not user.firebase_uid:
        return

    from firebase_admin import auth

    from core.auth.services import revoke_firebase_tokens, update_firebase_password

    firebase_uid = user.firebase_uid
    try:
        update_firebase_password(firebase_uid, new_password)
    except auth.UserNotFoundError:
        user.firebase_uid = None
        logger.warning(
            "Cleared stale Firebase UID during admin password reset for user %s",
            user.id,
        )
        return
    except Exception:
        logger.warning(
            "Firebase password update skipped during admin password reset for user %s",
            user.id,
            exc_info=True,
        )
        return

    try:
        revoke_firebase_tokens(firebase_uid)
    except Exception:  # nosec B110 -- best-effort Firebase token revocation
        pass

async def admin_forgot_password(payload: AdminForgotPasswordRequest, db: AsyncSession) -> ApiResponse:
    from core.email_service import send_reset_password_email
    from apps.accounts.db_models import PasswordResetToken
    from core.auth.config import settings as auth_settings
    import os
    import uuid

    email = payload.email.lower()
    stmt = select(User).options(selectinload(User.roles)).where(User.email == email)
    user = (await db.execute(stmt)).scalar_one_or_none()

    from apps.accounts.services.common_service import is_soft_deleted_user

    if not user or is_soft_deleted_user(user):
        return ApiResponse(status=False, message="User not found", data=None)

    # Check for recent active token to rate limit
    now = datetime.now(timezone.utc)
    existing_stmt = select(PasswordResetToken).where(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.used_at == None,
        PasswordResetToken.expires_at > now
    ).limit(1)
    # existing_token = (await db.execute(existing_stmt)).scalar_one_or_none()
    # if existing_token:
    #     return ApiResponse(
    #         status=False,
    #         message=f"Recently email for resest password as been send please try after {auth_settings.password_reset_token_expire_minutes} minutes  ",
    #         data=None
    #     )

    # Generate token
    token_val = str(uuid.uuid4())
    expires_at = now + timedelta(minutes=auth_settings.password_reset_token_expire_minutes)

    reset_token = PasswordResetToken(
        user_id=user.id,
        token=token_val,
        expires_at=expires_at,
    )
    db.add(reset_token)
    await db.commit()

    # Get application link
    app_link = os.getenv("APPLICATION_LINK").rstrip("/") + "/"
    reset_link = f"{app_link}reset-password?token={token_val}"

    first_name = getattr(user, "first_name", None)
    if not first_name and hasattr(user, "id"):
        try:
            from apps.profiles.db_models import Profile
            profile_stmt = select(Profile.first_name).where(Profile.user_id == user.id)
            first_name = (await db.execute(profile_stmt)).scalar_one_or_none()
        except Exception: # nosec B110
            pass

    # Send email
    try:
        await send_reset_password_email(email, reset_link, first_name=first_name)
    except TypeError:
        await send_reset_password_email(email, reset_link)

    return ApiResponse(status=True, message="Password reset link sent successfully to your mail ", data=None)

async def admin_reset_password(payload: AdminResetPasswordRequest, db: AsyncSession) -> ApiResponse:
    from apps.accounts.db_models import PasswordResetToken
    import uuid

    now = datetime.now(timezone.utc)

    try:
        token_uuid = uuid.UUID(payload.token)
    except ValueError:
        return ApiResponse(status=False, message="Invalid token format", data=None)

    stmt = select(PasswordResetToken).where(
        PasswordResetToken.token == str(token_uuid),
        PasswordResetToken.used_at == None,
    )
    reset_token = (await db.execute(stmt)).scalar_one_or_none()
    if not reset_token:
        return ApiResponse(status=False, message="Invalid reset password link", data=None)

    if reset_token.expires_at.replace(tzinfo=timezone.utc) < now:
        return ApiResponse(status=False, message="Your reset password link has expired.", data=None)

    user = (
        await db.execute(
            select(User).options(selectinload(User.roles)).where(User.id == reset_token.user_id)
        )
    ).scalar_one_or_none()
    if not user:
        return ApiResponse(status=False, message="User not found", data=None)

    user.password_hash = PASSWORD_HASHER.hash(payload.new_password)
    user.updated_at = now
    db.add(user)

    _sync_firebase_password_best_effort(user, payload.new_password)
    if user.firebase_uid is None:
        db.add(user)

    try:
        from apps.accounts.db_models import RefreshToken
        from apps.accounts.services.common_service import _revoke_refresh_token_row
        rows = (
            await db.execute(
                select(RefreshToken).where(
                    RefreshToken.user_id == user.id,
                    RefreshToken.revoked_at == None,  # noqa: E711
                )
            )
        ).scalars().all()
        for row in rows:
            await _revoke_refresh_token_row(db, row)
    except Exception:  # nosec B110 -- best-effort refresh token cleanup
        pass

    try:
        from apps.administration.services.session_service import (
            revoke_all_admin_sessions_for_user,
        )

        await revoke_all_admin_sessions_for_user(db, user.id)
    except Exception:  # nosec B110 -- best-effort admin session revocation
        pass

    reset_token.used_at = now
    db.add(reset_token)
    await db.commit()

    return ApiResponse(status=True, message="Password reset successful", data=None)

async def change_password(
    payload: ChangePasswordRequest,
    current_user: User,
    db: AsyncSession
) -> ApiResponse:

    if not current_user.password_hash or not PASSWORD_HASHER.verify(payload.current_password, current_user.password_hash):
        return ApiResponse(
            status=False,
            message="existing password does not match",
            data=None
        )

    if payload.current_password == payload.new_password:
        return ApiResponse(
            status=False,
            message="New password cannot be the same as current password",
            data=None
        )

    now = datetime.now(timezone.utc)
    current_user.password_hash = PASSWORD_HASHER.hash(payload.new_password)
    current_user.updated_at = now

    if current_user.firebase_uid:
        from core.auth.services import update_firebase_password, revoke_firebase_tokens
        try:
            update_firebase_password(current_user.firebase_uid, payload.new_password)
        except Exception:  # nosec B110 -- best-effort Firebase password update
            pass
        try:
            revoke_firebase_tokens(current_user.firebase_uid)
        except Exception:  # nosec B110 -- best-effort Firebase token revocation
            pass

    try:
        from apps.accounts.db_models import RefreshToken
        from apps.accounts.services.common_service import _revoke_refresh_token_row
        rows = (
            await db.execute(
                select(RefreshToken).where(
                    RefreshToken.user_id == current_user.id,
                    RefreshToken.revoked_at == None,  # noqa: E711
                )
            )
        ).scalars().all()
        for row in rows:
            await _revoke_refresh_token_row(db, row)
    except Exception:  # nosec B110 -- best-effort refresh token cleanup
        pass

    try:
        from apps.administration.services.session_service import (
            revoke_all_admin_sessions_for_user,
        )

        await revoke_all_admin_sessions_for_user(db, current_user.id)
    except Exception:  # nosec B110 -- best-effort admin session revocation
        pass

    db.add(current_user)
    await db.commit()

    return ApiResponse(
        status=True,
        message="Password updated successfully. Please sign in again.",
        data=None,
    )
