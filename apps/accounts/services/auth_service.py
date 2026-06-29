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
from common.enums import UserStatus
from core.auth.config import settings as auth_settings
from core.email_service import send_otp_email, send_verification_success_email, build_email_verified_success_html
from ..schemas import ApiResponse, LoginRequest, ResendOtpRequest, OtpVerifyRequest, UserBaseResponse
PASSWORD_HASHER = PasswordHash((BcryptHasher(),))

from .common_service import _fetch_user_profile, _generate_otp, _now

async def _issue_auth_session(user: User, db: AsyncSession) -> dict:
    profile = await _fetch_user_profile(db, user)
    from apps.profiles.services import build_user_base_response
    user_data = await build_user_base_response(user, profile, db)
    await db.commit()
    return {
        "user": user_data,
        "emailSent": False,
    }

async def build_firebase_session_response(user: User, db: AsyncSession) -> dict:
    profile = await _fetch_user_profile(db, user)
    from apps.profiles.services import build_user_base_response
    user_data = await build_user_base_response(user, profile, db)
    return {
        "user": user_data,
        "emailSent": False,
    }

async def login(payload: LoginRequest, firebase_user: dict, db: AsyncSession) -> ApiResponse:
    firebase_uid = firebase_user.get("uid")
    if not firebase_uid:
        return ApiResponse(status=False, message="User not registed yet", data=None)

    stmt = select(User).options(selectinload(User.roles)).where(User.firebase_uid == firebase_uid)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user:
        return ApiResponse(status=False, message=" Please complete signup", data=None)

    if not user.password_hash or not PASSWORD_HASHER.verify(payload.password, user.password_hash):
        return ApiResponse(status=False, message="Password not matched ", data=None)

    if user.deleted_at:
        return ApiResponse(status=False, message="Account is not active", data=None)

    if user.status in (UserStatus.suspended, UserStatus.banned):
        return ApiResponse(status=False, message="Account is either Suspended or banned ", data=None)

    device_id = payload.device_id.strip()
    if not device_id:
        return ApiResponse(status=False, message="device_id is required", data=None)

    from apps.accounts.db_models import UserInstallation
    stmt_install = select(UserInstallation).where(
        UserInstallation.user_id == user.id,
        UserInstallation.device_id == device_id
    )
    installation = (await db.execute(stmt_install)).scalar_one_or_none()

    is_new_device = installation is None
    needs_email_otp = user.email_verified_at is None
    needs_otp = needs_email_otp or is_new_device

    if needs_otp:
        now = _now()
        otp = _generate_otp()
        # Re-read DB state so a concurrent verify_otp commit is not overwritten.
        await db.refresh(user)
        user.email_otp = otp
        user.email_otp_created_at = now
        if user.email_verified_at is None:
            user.status = UserStatus.pending
        user.updated_at = now
        db.add(user)

        if is_new_device:
            new_install = UserInstallation(
                user_id=user.id,
                device_id=device_id,
                platform=None,  # let platform be null rather than unknown for now
                app_version=None,
                installed_at=now,
                last_active_at=now,
                is_active=True,
            )
            db.add(new_install)
        elif installation:
            installation.last_active_at = now
            installation.is_active = True
            db.add(installation)

        await db.commit()
        await send_otp_email(user.email, otp, "email_verification")

        # Load roles eagerly to avoid MissingGreenlet when accessing user.role
        stmt_user = select(User).options(selectinload(User.roles)).where(User.id == user.id)
        user = (await db.execute(stmt_user)).scalar_one()

        data = await _issue_auth_session(user, db)
        if isinstance(data, dict):
            data["emailSent"] = True
        return ApiResponse(status=True, message="Verification email sent. Please verify your OTP.", data=data)

    else:
        user.status = UserStatus.active
        user.updated_at = _now()
        db.add(user)

        if installation:
            installation.last_active_at = _now()
            installation.is_active = True
            db.add(installation)

        await db.commit()

        # Load roles eagerly to avoid MissingGreenlet when accessing user.role
        stmt_user = select(User).options(selectinload(User.roles)).where(User.id == user.id)
        user = (await db.execute(stmt_user)).scalar_one()

        return ApiResponse(status=True, message="Login successful", data=await _issue_auth_session(user, db))

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
        user.email_verified_at = _now()
        user.status = UserStatus.active
        user.email_otp = _generate_otp()
        user.email_otp_created_at = _now()
        db.add(user)
        await db.commit()

        stmt_profile = select(Profile).where(Profile.user_id == user.id)
        profile = (await db.execute(stmt_profile)).scalar_one_or_none()
        full_name = f"{profile.first_name or ''} {profile.last_name or ''}".strip() if profile else None

        html_content = build_email_verified_success_html(full_name)
        # Send a verification success email using existing account_created_email template
        from core.email_service import send_verification_success_email
        await send_verification_success_email(user.email, full_name)
        return ApiResponse(status=True, message="OTP successfully verified", data=None)

    return ApiResponse(status=False, message="Email not verified in Firebase yet", data=None)

async def verify_email(token: str, db: AsyncSession) -> HTMLResponse | ApiResponse:
    stmt = select(User).where(User.email_otp == token)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user:
        return HTMLResponse(content="<h1>Invalid or expired token</h1>", status_code=400)

    if user.email_otp_created_at and (_now() - user.email_otp_created_at) > timedelta(minutes=auth_settings.otp_expire_minutes):
        return HTMLResponse(content="<h1>Token expired</h1>", status_code=400)

    user.email_verified_at = _now()
    user.status = UserStatus.active
    user.email_otp = "true"  # Set users.email_otp = "true" as requested
    user.email_otp_created_at = _now()
    db.add(user)
    await db.commit()

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
        referenceCode="",
        invitationCode=None,
    )
