from __future__ import annotations
import logging
import os
from datetime import timezone, timedelta
from uuid import uuid4, UUID
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from pwdlib import PasswordHash
from pwdlib.hashers.bcrypt import BcryptHasher
from apps.accounts.db_models import User, PasswordResetToken
from core.auth.config import settings as auth_settings
from core.email_service import send_reset_password_email
from ..schemas import ApiResponse, ResetPasswordRequest, ForgotPasswordRequest, UserChangePasswordRequest
from core.auth.services import update_firebase_password, verify_firebase_token
logger = logging.getLogger(__name__)
from dotenv import load_dotenv

load_dotenv()

PASSWORD_HASHER = PasswordHash((BcryptHasher(),))

from .common_service import _now

async def forgot_password( payload: ForgotPasswordRequest,db: AsyncSession ) -> ApiResponse:

    email = payload.email.lower()

    stmt = select(User).where(User.email == email)
    user = (await db.execute(stmt)).scalar_one_or_none()

    if not user:
        return ApiResponse(
            status=True,
            message="If an account exists for this email, a password reset link has been sent.",
            data=None
        )

    now = _now()

    existing_stmt = select(PasswordResetToken).where(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.used_at == None,
        PasswordResetToken.expires_at > now
    ).limit(1)

    existing_token = (
        await db.execute(existing_stmt)
    ).scalar_one_or_none()

    if existing_token:
        return ApiResponse(
            status=False,
            message=f"Recently email for reset password has been sent. Please try after {auth_settings.password_reset_token_expire_minutes} mins",
            data=None
        )

    token_val = str(uuid4())

    reset_token = PasswordResetToken(
        user_id=user.id,
        token=token_val,
        expires_at=now + timedelta(
            minutes=auth_settings.password_reset_token_expire_minutes
        )
    )

    db.add(reset_token)
    await db.commit()

    app_link = os.getenv(
        "APPLICATION_LINK",
    ).rstrip("/") + "/"

    reset_link = (
        f"{app_link}reset-password?token={token_val}"
    )
    await send_reset_password_email(
        email,
        reset_link
    )

    return ApiResponse(
        status=True,
        message="Password reset link sent successfully to your mail",
        data=None
)

async def reset_password(payload: ResetPasswordRequest,  db: AsyncSession    ) -> ApiResponse:

    if not payload.token and not payload.firebaseId:
        return ApiResponse(
            status=False,
            message="Token or firebaseId is required",
            data=None
        )

    user = None
    reset_token = None
    now = _now()

    # TOKEN FLOW
    if payload.token:

        try:
            token_uuid = UUID(payload.token)
        except ValueError:
            return ApiResponse(
                status=False,
                message="Invalid token format",
                data=None
            )

        stmt = select(PasswordResetToken).where(
            PasswordResetToken.token == str(token_uuid),
            PasswordResetToken.used_at == None
        )

        reset_token = (
            await db.execute(stmt)
        ).scalar_one_or_none()

        if not reset_token:
            return ApiResponse(
                status=False,
                message="Invalid reset password link",
                data=None
            )

        if reset_token.expires_at.replace(
            tzinfo=timezone.utc
        ) < now:
            return ApiResponse(
                status=False,
                message="Your reset password link has expired.",
                data=None
            )

        user = await db.get(
            User,
            reset_token.user_id
        )

        # FIREBASE FLOW

    elif payload.firebaseId:

        try:
            decoded_token = verify_firebase_token(
                payload.firebaseId
            )

            firebase_uid = decoded_token.get("uid")

            stmt = select(User).where(
                User.firebase_uid == firebase_uid
            )

            user = (
                await db.execute(stmt)
            ).scalar_one_or_none()

        except Exception:
            return ApiResponse(
                status=False,
                message="Invalid firebase authentication",
                data=None
            )

    if not user:
        return ApiResponse(
            status=False,
            message="User not found",
            data=None
        )

    try:
        update_firebase_password(
            user.firebase_uid,
            password=payload.new_password
        )
    except Exception as exc:
        return ApiResponse(
            status=False,
            message=f"Failed to update password in firebase: {str(exc)}",
            data=None
        )

    user.password_hash = PASSWORD_HASHER.hash(
        payload.new_password
    )
    user.updated_at = now

    db.add(user)

    if reset_token:
        reset_token.used_at = now
        db.add(reset_token)

    await db.commit()

    return ApiResponse(
        status=True, message="Password reset successful", data=None
    )

async def change_password(payload: UserChangePasswordRequest, db: AsyncSession) -> ApiResponse:

    try:
        decoded_token = verify_firebase_token(payload.firebaseId)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid firebase token"
        )

    firebase_uid = decoded_token.get("uid")

    stmt = select(User).where(
        User.firebase_uid == firebase_uid
    )

    user = (
        await db.execute(stmt)
    ).scalar_one_or_none()

    if not user:
        return ApiResponse(
            status=False,
            message="User not found",
            data=None
        )

    if not user.password_hash or not PASSWORD_HASHER.verify(payload.current_password, user.password_hash):
        return ApiResponse(
            status=False,
            message="Existing password does not match",
            data=None
        )

    user.password_hash = PASSWORD_HASHER.hash(
        payload.new_password
    )
    user.updated_at = _now()

    try:
        update_firebase_password(
            user.firebase_uid,
            password=payload.new_password
        )
    except Exception as e:
        logger.error(f"Failed to update firebase password: {e}")
        return ApiResponse(
            status=False,
            message="Failed to update password in Firebase",
            data=None
        )

    await db.commit()

    return ApiResponse(
        status=True,
        message="Password updated successfully",
        data=None
    )
