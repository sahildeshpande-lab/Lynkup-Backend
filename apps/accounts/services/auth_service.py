from __future__ import annotations
from datetime import timedelta
from uuid import uuid4
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from sqlalchemy.orm import selectinload
from pwdlib import PasswordHash
from pwdlib.hashers.bcrypt import BcryptHasher
from apps.accounts.db_models import User, UserInstallation
from apps.profiles.db_models import Profile
from common.enums import OnboardingStatus, UserStatus, inactive_account_message
from core.auth.config import settings as auth_settings
from core.email_service import send_otp_email, send_verification_success_email, build_email_verified_success_html
from ..schemas import ApiResponse, LoginRequest, ResendOtpRequest, OtpVerifyRequest, UserBaseResponse
PASSWORD_HASHER = PasswordHash((BcryptHasher(),))

from .common_service import _fetch_user_profile, _generate_otp, _now
from .device_otp_service import (
    attach_otp_flags,
    evaluate_device_otp_requirement,
    send_otp_challenge,
)

async def _issue_auth_session(user: User, db: AsyncSession) -> dict:
    profile = await _fetch_user_profile(db, user)
    from apps.profiles.services import build_user_base_response
    user_data = await build_user_base_response(user, profile, db)
    await db.commit()
    return {
        "user": user_data,
        "emailSent": False,
        "needsOtp": user.email_verified_at is None,
    }

async def build_firebase_session_response(user: User, db: AsyncSession) -> dict:
    profile = await _fetch_user_profile(db, user)
    from apps.profiles.services import build_user_base_response
    user_data = await build_user_base_response(user, profile, db)
    return {
        "user": user_data,
        "emailSent": False,
        "needsOtp": user.email_verified_at is None,
    }

async def login(payload: LoginRequest, firebase_user: dict, db: AsyncSession) -> ApiResponse:
    firebase_uid = firebase_user.get("uid")
    if not firebase_uid:
        return ApiResponse(status=False, message="User not registed yet", data=None)

    stmt = select(User).options(selectinload(User.roles)).where(User.firebase_uid == firebase_uid)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user:
        return ApiResponse(status=False, message=" Please complete signup", data=None)

    # Email/password login must match the account email on the Firebase token and DB.
    login_email = payload.email.lower().strip()
    firebase_email = (firebase_user.get("email") or "").lower().strip()
    if user.email.lower() != login_email:
        return ApiResponse(status=False, message="Invalid credentials", data=None)
    if firebase_email and firebase_email != login_email:
        return ApiResponse(status=False, message="Invalid credentials", data=None)

    # Social accounts have no password_hash — they must use /auth/social (or reset password).
    if not user.password_hash:
        reg_type = (
            user.registration_type.value
            if hasattr(user.registration_type, "value")
            else str(user.registration_type or "")
        ).lower()
        if reg_type == "google":
            return ApiResponse(
                status=False,
                message="This account uses Google Sign-In. Use Google or reset your password.",
                data=None,
            )
        if reg_type in ("apple", "ios"):
            return ApiResponse(
                status=False,
                message="This account uses Apple Sign-In. Use Apple or reset your password.",
                data=None,
            )
        return ApiResponse(status=False, message="Invalid credentials", data=None)

    if not PASSWORD_HASHER.verify(payload.password, user.password_hash):
        return ApiResponse(status=False, message="Invalid credentials", data=None)

    if user.status == UserStatus.deleting or user.deleted_at:
        return ApiResponse(status=False, message=inactive_account_message(UserStatus.deleting), data=None)

    if user.status in (UserStatus.suspended, UserStatus.banned):
        return ApiResponse(status=False, message=inactive_account_message(user.status), data=None)

    device_id = payload.device_id.strip()
    if not device_id:
        return ApiResponse(status=False, message="device_id is required", data=None)

    installation, is_new_device, needs_otp = await evaluate_device_otp_requirement(
        db,
        user,
        device_id,
    )

    if needs_otp:
        await send_otp_challenge(
            db,
            user,
            device_id,
            installation=installation,
            is_new_device=is_new_device,
        )

        stmt_user = select(User).options(selectinload(User.roles)).where(User.id == user.id)
        user = (await db.execute(stmt_user)).scalar_one()

        data = attach_otp_flags(
            await _issue_auth_session(user, db),
            email_sent=True,
            needs_otp=True,
        )
        return ApiResponse(
            status=True,
            message="Verification email sent. Please verify your OTP.",
            data=data,
        )

    user.status = UserStatus.active
    user.updated_at = _now()
    db.add(user)

    if installation:
        installation.last_active_at = _now()
        installation.is_active = True
        db.add(installation)

    await db.commit()

    stmt_user = select(User).options(selectinload(User.roles)).where(User.id == user.id)
    user = (await db.execute(stmt_user)).scalar_one()

    return ApiResponse(
        status=True,
        message="Login successful",
        data=attach_otp_flags(
            await _issue_auth_session(user, db),
            email_sent=False,
            needs_otp=False,
        ),
    )

async def verify_otp(payload: OtpVerifyRequest, firebase_user: dict, db: AsyncSession):
    firebase_uid = firebase_user["uid"]
    stmt = select(User).where(User.firebase_uid == firebase_uid)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user:
        return ApiResponse(status=False, message="User not found", data=None)

    if user.email.lower() != payload.email.lower():
        return ApiResponse(status=False, message="Email does not match user", data=None)

    if user.email_otp != payload.otp:
        return ApiResponse(status=False, message="Incorrect OTP. Please try again", data=None)

    if user.email_otp_created_at and (_now() - user.email_otp_created_at) > timedelta(minutes=auth_settings.otp_expire_minutes):
        return ApiResponse(status=False, message="OTP has expired. Please request a new OTP", data=None)

    if user.email_otp == payload.otp:
        onboarding_completed = user.onboarding_status == OnboardingStatus.completed
        user.email_verified_at = _now()
        user.status = UserStatus.active
        user.email_otp = None
        user.email_otp_created_at = None
        db.add(user)
        await db.commit()

        if not onboarding_completed:
            stmt_profile = select(Profile).where(Profile.user_id == user.id)
            profile = (await db.execute(stmt_profile)).scalar_one_or_none()
            full_name = f"{profile.first_name or ''} {profile.last_name or ''}".strip() if profile else None
            await send_verification_success_email(user.email, full_name)

        stmt_user = select(User).options(selectinload(User.roles)).where(User.id == user.id)
        user = (await db.execute(stmt_user)).scalar_one()

        from apps.chat.service import sync_stream_user_on_auth

        await sync_stream_user_on_auth(user, db)

        data = attach_otp_flags(
            await _issue_auth_session(user, db),
            email_sent=False,
            needs_otp=False,
        )
        return ApiResponse(status=True, message="OTP successfully verified", data=data)

    return ApiResponse(status=False, message="Email not verified in Firebase yet", data=None)

async def verify_email(token: str, db: AsyncSession) -> HTMLResponse | ApiResponse:
    stmt = select(User).where(User.email_otp == token)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user:
        return HTMLResponse(content="<h1>Invalid or expired token</h1>", status_code=400)

    if user.email_otp_created_at and (_now() - user.email_otp_created_at) > timedelta(minutes=auth_settings.otp_expire_minutes):
        return HTMLResponse(content="<h1>Token expired</h1>", status_code=400)

    onboarding_completed = user.onboarding_status == OnboardingStatus.completed
    user.email_verified_at = _now()
    user.status = UserStatus.active
    user.email_otp = None
    user.email_otp_created_at = None
    db.add(user)
    await db.commit()

    if onboarding_completed:
        return HTMLResponse(content="", status_code=200)

    stmt_profile = select(Profile).where(Profile.user_id == user.id)
    profile = (await db.execute(stmt_profile)).scalar_one_or_none()
    full_name = f"{profile.first_name or ''} {profile.last_name or ''}".strip() if profile else None
    html_content = build_email_verified_success_html(full_name)
    return HTMLResponse(content=html_content, status_code=200)

async def resend_otp(payload: ResendOtpRequest, firebase_user: dict, db: AsyncSession) -> ApiResponse:
    stmt = select(User).where(User.email == payload.email.lower())
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user:
        return ApiResponse(status=False, message="User not found", data=None)

    if user.firebase_uid != firebase_user["uid"]:
        return ApiResponse(status=False, message="Unauthorized action for this user account", data=None)

    # Enforce cooldown based on configuration (default 2 minutes)
    # cooldown = timedelta(minutes=auth_settings.resend_otp_cooldown_minutes)
    # if user.email_otp_created_at and (_now() - user.email_otp_created_at) < cooldown:
    #       return ApiResponse(status=False, message="Please wait for 10 mins before resending OTP. A verification code has already been sent to your email.", data=None)
    otp = _generate_otp()
    user.email_otp = otp
    user.email_otp_created_at = _now()
    user.updated_at = _now()
    db.add(user)
    await db.commit()
    await send_otp_email(user.email, otp, "email_verification")
    return ApiResponse(status=True, message="OTP sent successfully", data=None)

def _build_user_base(refresh_token: str) -> UserBaseResponse:
    now = _now()
    normalized_seed = refresh_token.replace("refresh_", "").strip().lower() or "user"
    primary_email = normalized_seed if "@" in normalized_seed else f"{normalized_seed}@example.com"
    return UserBaseResponse(
        id=str(uuid4()),
        email=primary_email,
        createdAt=now,
        updatedAt=now,
    )
