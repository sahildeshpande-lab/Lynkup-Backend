from __future__ import annotations
from datetime import datetime, timedelta, timezone
from pwdlib import PasswordHash
from pwdlib.hashers.bcrypt import BcryptHasher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from apps.accounts.db_models import User
from ..schemas import ChangePasswordRequest, AdminForgotPasswordRequest, AdminResetPasswordRequest
from apps.accounts.schemas import ApiResponse
from sqlalchemy.orm import selectinload
PASSWORD_HASHER = PasswordHash((BcryptHasher(),))

async def admin_forgot_password(payload: AdminForgotPasswordRequest, db: AsyncSession) -> ApiResponse:
    from core.email_service import send_reset_password_email
    from apps.accounts.db_models import PasswordResetToken
    from core.auth.config import settings as auth_settings
    import os
    import uuid

    email = payload.email.lower()
    stmt = select(User).options(selectinload(User.roles)).where(User.email == email)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user or user.role == "user":
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
    #         message=f"Recently email for resest password as been send please try after {auth_settings.password_reset_token_expire_minutes} mins  ",
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

    # Send email
    await send_reset_password_email(email, reset_link)

    return ApiResponse(status=True, message="Password reset link sent successfully to your mail ", data=None)

async def admin_reset_password(payload: AdminResetPasswordRequest, db: AsyncSession) -> ApiResponse:
    from apps.accounts.db_models import PasswordResetToken
    import uuid
    now=datetime.now(timezone.utc)

    if not payload.token:
        return ApiResponse(status=False, message="Token is required", data=None)

    if payload.token  :
        try:
            # Check if it is a valid UUID string
            token_uuid = uuid.UUID(payload.token)
        except ValueError:
            return ApiResponse(status=False, message="Invalid token format", data=None)

        stmt = select(PasswordResetToken).where(
            PasswordResetToken.token == str(token_uuid),
            PasswordResetToken.used_at == None
        )
        reset_token = (await db.execute(stmt)).scalar_one_or_none()
        if not reset_token:
            return ApiResponse(status=False, message="Invalid reset password link", data=None)

        now = datetime.now(timezone.utc)
        if reset_token.expires_at.replace(tzinfo=timezone.utc) < now:
            return ApiResponse(status=False, message="Your reset password link has expired.", data=None)

        user = (await db.execute(select(User).options(selectinload(User.roles)).where(User.id == reset_token.user_id))).scalar_one_or_none()
        if not user or user.role == "user":
            return ApiResponse(status=False, message="User not found", data=None)

        user.password_hash = PASSWORD_HASHER.hash(payload.new_password)
        user.updated_at = now
        db.add(user)

        # Invalidate token
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

    current_user.password_hash = PASSWORD_HASHER.hash(
        payload.new_password
    )

    current_user.updated_at = datetime.now(timezone.utc)

    if current_user.firebase_uid:
        from core.auth.services import update_firebase_password
        try:
            update_firebase_password(current_user.firebase_uid, payload.new_password)
        except Exception:
            pass

    await db.commit()

    return ApiResponse(
        status=True,
        message="Password updated successfully",
        data=None
    )
