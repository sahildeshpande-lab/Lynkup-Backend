from __future__ import annotations
import logging
from datetime import timedelta
from uuid import uuid4
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from sqlalchemy.orm import selectinload
from pwdlib import PasswordHash
from pwdlib.hashers.bcrypt import BcryptHasher
from apps.accounts.db_models import User
from apps.profiles.db_models import Profile
from common.enums import OnboardingStatus, UserStatus, inactive_account_message
from core.auth.config import settings as auth_settings
from core.email_service import send_otp_email, send_verification_success_email, build_email_verified_success_html
from ..schemas import ApiResponse, LoginRequest, ResendOtpRequest, OtpVerifyRequest, UserBaseResponse
logger = logging.getLogger(__name__)
PASSWORD_HASHER = PasswordHash((BcryptHasher(),))

from .common_service import (
    _fetch_user_profile,
    _generate_otp,
    _now,
    is_soft_deleted_user,
    reactivate_soft_deleted_user,
)
from .device_otp_service import (
    attach_otp_flags,
    begin_otp_challenge,
    ensure_unverified_installation,
    evaluate_device_otp_requirement,
    mark_pending_active_device_verified,
    upsert_user_installation,
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
        "isDeviceVerified": False,
    }

async def build_firebase_session_response(user: User, db: AsyncSession) -> dict:
    profile = await _fetch_user_profile(db, user)
    from apps.profiles.services import build_user_base_response
    user_data = await build_user_base_response(user, profile, db)
    return {
        "user": user_data,
        "emailSent": False,
        "needsOtp": user.email_verified_at is None,
        "isDeviceVerified": False,
    }

async def login(payload: LoginRequest, firebase_user: dict, db: AsyncSession) -> ApiResponse:
    firebase_uid = firebase_user.get("uid")
    if not firebase_uid:
        return ApiResponse(status=False, message="User not registed yet", data=None)

    login_email = payload.email.lower().strip()
    firebase_email = (firebase_user.get("email") or "").lower().strip()

    stmt = select(User).options(selectinload(User.roles)).where(User.firebase_uid == firebase_uid)
    user = (await db.execute(stmt)).scalar_one_or_none()

    # After Firebase user deletion, soft-deleted rows keep a stale firebase_uid.
    # Re-link by email when the new Firebase account signs in again.
    if not user:
        stmt_email = select(User).options(selectinload(User.roles)).where(User.email == login_email)
        candidate = (await db.execute(stmt_email)).scalar_one_or_none()
        if candidate is not None and is_soft_deleted_user(candidate):
            user = candidate
        else:
            return ApiResponse(status=False, message=" Please complete signup", data=None)

    # Email/password login must match the account email on the Firebase token and DB.
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

    if user.status in (UserStatus.suspended, UserStatus.banned):
        return ApiResponse(status=False, message=inactive_account_message(user.status), data=None)

    if is_soft_deleted_user(user):
        reactivate_soft_deleted_user(
            user,
            now=_now(),
            firebase_uid=firebase_uid,
        )
        db.add(user)
        await db.flush()

    device_id = (payload.device_id or "").strip() or None

    installation, is_new_device, needs_otp = await evaluate_device_otp_requirement(
        db,
        user,
        device_id,
    )

    if needs_otp:
        email_sent = await begin_otp_challenge(
            db,
            user,
            device_id,
            installation=installation,
            is_new_device=is_new_device,
            platform=payload.platform,
            fcm_token=payload.fcm_token,
        )

        # Best-effort sync: never fail login if Firebase sync fails.
        try:
            from apps.notifications.services.topic_service import TopicService

            profile = await _fetch_user_profile(db, user)
            if profile is not None:
                await TopicService.refresh_user_topic_subscriptions(db, user.id, profile)
        except Exception:
            logger.exception(
                "Firebase topic sync failed during login (OTP flow) user_id=%s",
                user.id,
            )

        stmt_user = select(User).options(selectinload(User.roles)).where(User.id == user.id)
        user = (await db.execute(stmt_user)).scalar_one()

        data = attach_otp_flags(
            await _issue_auth_session(user, db),
            email_sent=email_sent,
            needs_otp=True,
            is_device_verified=False,
        )
        message = (
            "Verification email sent. Please verify your OTP."
            if email_sent
            else "Please verify your OTP."
        )
        return ApiResponse(
            status=True,
            message=message,
            data=data,
        )

    user.status = UserStatus.active
    user.updated_at = _now()
    db.add(user)

    if device_id:
        await upsert_user_installation(
            db,
            user.id,
            device_id,
            platform=payload.platform,
            fcm_token=payload.fcm_token,
            now=_now(),
        )

    await db.commit()

    # Best-effort sync: never fail login if Firebase sync fails.
    try:
        from apps.notifications.services.topic_service import TopicService

        profile = await _fetch_user_profile(db, user)
        if profile is not None:
            await TopicService.refresh_user_topic_subscriptions(db, user.id, profile)
    except Exception:
        logger.exception(
            "Firebase topic sync failed during login (no-OTP flow) user_id=%s",
            user.id,
        )

    try:
        from apps.chat.service import sync_stream_user_on_auth

        await sync_stream_user_on_auth(user, db)
    except Exception:
        logger.exception(
            "Stream user sync failed during login (no-OTP flow) user_id=%s",
            user.id,
        )

    stmt_user = select(User).options(selectinload(User.roles)).where(User.id == user.id)
    user = (await db.execute(stmt_user)).scalar_one()

    return ApiResponse(
        status=True,
        message="Login successful",
        data=attach_otp_flags(
            await _issue_auth_session(user, db),
            email_sent=False,
            needs_otp=False,
            is_device_verified=True,
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
        now = _now()
        onboarding_completed = user.onboarding_status == OnboardingStatus.completed
        user.email_verified_at = now
        user.status = UserStatus.active
        user.email_otp = None
        user.email_otp_created_at = None
        user.updated_at = now
        db.add(user)

        # Trust latest pending install by last_active_at (from login device_id).
        # Previously verified devices are not demoted — they still skip OTP.
        installation = await mark_pending_active_device_verified(
            db,
            user.id,
            now=now,
        )
        device_verified = installation is not None
        await db.commit()

        if not onboarding_completed:
            stmt_profile = select(Profile).where(Profile.user_id == user.id)
            profile = (await db.execute(stmt_profile)).scalar_one_or_none()
            full_name = f"{profile.first_name or ''} {profile.last_name or ''}".strip() if profile else None
            await send_verification_success_email(user.email, full_name)

        try:
            from apps.notifications.services.topic_service import TopicService

            stmt_profile = select(Profile).where(Profile.user_id == user.id)
            profile = (await db.execute(stmt_profile)).scalar_one_or_none()
            if profile is not None:
                await TopicService.refresh_user_topic_subscriptions(db, user.id, profile)
        except Exception:
            logger.exception(
                "Firebase topic sync failed during OTP verification user_id=%s",
                user.id,
            )

        try:
            from apps.chat.service import sync_stream_user_on_auth

            await sync_stream_user_on_auth(user, db)
        except Exception:
            logger.exception(
                "Stream user sync failed during OTP verification user_id=%s",
                user.id,
            )

        stmt_user = select(User).options(selectinload(User.roles)).where(User.id == user.id)
        user = (await db.execute(stmt_user)).scalar_one()
        data = attach_otp_flags(
            await _issue_auth_session(user, db),
            email_sent=False,
            needs_otp=False,
            is_device_verified=device_verified,
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

    device_id = (payload.device_id or "").strip() or None
    now = _now()
    # Keep the installation unverified; only /verify-otp may trust a device.
    if device_id:
        await ensure_unverified_installation(
            db,
            user.id,
            device_id,
            platform=payload.platform,
            now=now,
        )

    otp = _generate_otp()
    user.email_otp = otp
    user.email_otp_created_at = now
    user.updated_at = now
    db.add(user)
    await db.commit()
    profile = await _fetch_user_profile(db, user)
    full_name = (
        f"{(profile.first_name or '').strip()} {(profile.last_name or '').strip()}".strip()
        if profile
        else None
    ) or None
    await send_otp_email(user.email, otp, "email_verification", full_name=full_name)
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
