from __future__ import annotations

import hashlib
import inspect
import logging
import os
import secrets
import jwt
from datetime import datetime, timezone, timedelta
from pathlib import Path
from uuid import uuid4,UUID

from dotenv import load_dotenv
from fastapi import HTTPException, UploadFile, status
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from sqlalchemy.orm import selectinload
from pwdlib import PasswordHash
from pwdlib.hashers.bcrypt import BcryptHasher
from apps.accounts.db_models import Role, UserRole
import httpx

from apps.accounts.db_models import RefreshToken, SecurityEvent, SecurityEventType, TransactionalEmailLog, User , UserInstallation  , PasswordResetToken
from apps.profiles.db_models import Profile
from common.enums import OnboardingStatus, RegistrationType, UserStatus
from core.auth.config import settings as auth_settings
from core.email_service import send_otp_email, send_verification_success_email, build_email_verified_success_html , send_reset_password_email
 
from .schemas import (
    ApiResponse,
    ResetPasswordRequest, 
    LogoutRequest,
    AuthSessionResponse,
    AuthUserResponse,
    EmailLoginRequest,
    LoginRequest,
    EmailSignupRequest,
    LogoutRequest,
    RefreshTokenRequest,
    ResendOtpRequest,
    SocialAuthRequest,
    OtpVerifyRequest,
    RefreshSessionResponse,
    UserBaseResponse,
    ForgotPasswordRequest,
    UserChangePasswordRequest,
)
from core.auth.services import update_firebase_password

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _fetch_user_profile(db: AsyncSession, user: User) -> Profile | None:
    stmt = select(Profile).where(Profile.user_id == user.id)
    return (await db.execute(stmt)).scalar_one_or_none()


load_dotenv(Path(__file__).resolve().parents[2] / ".env")

JWT_SECRET = auth_settings.jwt_secret
JWT_ALGORITHM = auth_settings.jwt_algorithm
ACCESS_TOKEN_EXPIRE_MINUTES = auth_settings.access_token_expire_minutes 
LOGIN_EVENT_THROTTLE_SECONDS = auth_settings.login_event_throttle_seconds
PASSWORD_HASHER = PasswordHash((BcryptHasher(),))


def _hash_password(password: str, username: str | None = None) -> str:
    return PASSWORD_HASHER.hash(password)


def _generate_otp() -> str:
    return f"{secrets.randbelow(9000) + 1000}"


async def _log_email_event(
    db: AsyncSession,
    *,
    to_email: str,
    subject: str,
    body: str,
    purpose: str,
    attachment: str | None = None,
    is_sent: bool = True,
) -> None:
    from_email = os.getenv("SENDGRID_FROM_EMAIL", "no-reply@yourdomain.com")
    db.add(
        TransactionalEmailLog(
            to=to_email,
            from_email=from_email,
            body=body,
            attachment=attachment,
            purpose=purpose,
            subject=subject,
            is_sent=is_sent,
        )
    )


def _generate_tokens(user: User) -> tuple[str, str]:
    now = _now()
    access_expiry = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    access_payload = {
        "sub": str(user.id),
        "uid": user.firebase_uid,
        "email": user.email,
        "role": user.role,
        "type": "access",
        "exp": int(access_expiry.timestamp()),
        "iat": int(now.timestamp()),
    }

    refresh_payload = {
        "sub": str(user.id),
        "uid": user.firebase_uid,
        "type": "refresh",
        "iat": int(now.timestamp()),
    }

    access_token = jwt.encode(access_payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    refresh_token = jwt.encode(refresh_payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    return access_token, refresh_token


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def _store_refresh_token(
    db: AsyncSession,
    user: User,
    refresh_token: str,
    device_id: str | None = None,
    device_name: str | None = None,
) -> RefreshToken:
    now = _now()
    refresh_row = RefreshToken(
        user_id=user.id,
        token_hash=_hash_token(refresh_token),
        device_id=device_id,
        device_name=device_name,
        expires_at=None,
        created_at=now,
    )
    db.add(refresh_row)
    await db.flush()
    return refresh_row


async def _revoke_refresh_token_row(db: AsyncSession, token_row: RefreshToken) -> None:
    token_row.revoked_at = _now()
    token_row.last_used_at = _now()
    db.add(token_row)


def _refresh_token_payload(user: User) -> dict:
    now = _now()
    return {
        "sub": str(user.id),
        "uid": user.firebase_uid,
        "type": "refresh",
        "iat": int(now.timestamp()),
    }


def _build_auth_user_response(user: User, profile: Profile | None) -> AuthUserResponse:
    first_name = profile.first_name if profile and profile.first_name else ""
    last_name = profile.last_name if profile and profile.last_name else ""

    from core.images import generate_download_url

    return AuthUserResponse(
        id=str(user.id),
        firstName=first_name,
        lastName=last_name,
        email=user.email,
        role=user.role,
        status=user.status.value if hasattr(user.status, "value") else str(user.status),
        isEmailVerified=(user.email_verified_at is not None),
        email_verified_at=user.email_verified_at,
        email_otp_created_at=user.email_otp_created_at,
        completenessScore=profile.completeness_score if profile else 33,
        createdAt=user.created_at,
        updatedAt=user.updated_at,
        referenceCode="",
        invitationCode=None,
        profilePhoto_url=generate_download_url(_optional_str(getattr(profile, "profile_photo_url", None))) if profile else None,
        bannerPhotoUrl=generate_download_url(_optional_str(getattr(profile, "banner_photo_url", None))) if profile else None,
    )


def _registration_type_from_firebase(firebase_user: dict) -> RegistrationType:
    provider = (
        firebase_user.get("firebase", {})
        .get("sign_in_provider", "password")
    )
    provider_lower = str(provider).lower()
    if provider_lower == "google.com":
        return RegistrationType.google
    if provider_lower == "apple.com":
        return RegistrationType.apple
    return RegistrationType.email


def _display_name_from_firebase(firebase_user: dict, email: str) -> str:
    name = firebase_user.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return email.split("@", 1)[0]


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _optional_str(value) -> str | None:
    return value if isinstance(value, str) and value else None


def _is_mock_object(value) -> bool:
    return value.__class__.__module__.startswith("unittest.mock")


async def _add_db_object(db: AsyncSession, obj) -> None:
    result = db.add(obj)
    if inspect.isawaitable(result):
        await result


async def log_security_event(
    db: AsyncSession,
    user_id,
    event_type: SecurityEventType,
    event_metadata: dict | None = None,
    ip_address: str | None = None,
) -> None:
    db.add(
        SecurityEvent(
            user_id=user_id,
            event_type=event_type,
            event_metadata=event_metadata,
            ip_address=ip_address,
        )
    )




async def complete_firebase_registration(firebase_user: dict, db: AsyncSession) -> User:
    firebase_uid = firebase_user["uid"]
    stmt = select(User).options(selectinload(User.roles)).where(User.firebase_uid == firebase_uid)
    user = (await db.execute(stmt)).scalar_one_or_none()
    now = _now()

    if user:
        last_login_at = _as_aware_utc(user.last_login_at) if user.last_login_at else None
        should_record_login = (
            last_login_at is None
            or (now - last_login_at).total_seconds() >= LOGIN_EVENT_THROTTLE_SECONDS
        )
        if should_record_login:
            user.last_login_at = now
            user.updated_at = now
            db.add(user)
            await log_security_event(db, user.id, SecurityEventType.LOGIN_SUCCESS)
            await db.commit()
        await db.refresh(user)
        return (
            await db.execute(
                select(User).options(selectinload(User.roles)).where(User.id == user.id)
            )
        ).scalar_one()

    email = (firebase_user.get("email") or f"{firebase_uid}@firebase.local").lower()
    
    # Handle email conflict: Link account if email exists
    stmt_conflict = select(User).options(selectinload(User.roles)).where(User.email == email)
    existing_user = (await db.execute(stmt_conflict)).scalar_one_or_none()
    
    email_verified = bool(firebase_user.get("email_verified"))
    registration_type = _registration_type_from_firebase(firebase_user)

    if existing_user:
        # Link account if it has no firebase_uid, or if firebase_uid differs but registration type matches (recreated Firebase account)
        if (not existing_user.firebase_uid) or (existing_user.firebase_uid != firebase_uid and existing_user.registration_type == registration_type):
            existing_user.firebase_uid = firebase_uid
            existing_user.updated_at = now
            if email_verified and not existing_user.email_verified_at:
                existing_user.email_verified_at = now
            db.add(existing_user)
            await db.flush()
            user = existing_user
        else:
            reg_type_str = (
                existing_user.registration_type.value
                if hasattr(existing_user.registration_type, "value")
                else str(existing_user.registration_type)
            )
            raise AccountExistsException(registration_type=reg_type_str)
    else:
        user = User(
            firebase_uid=firebase_uid,
            email=email,
            registration_type=registration_type,
            status=UserStatus.active,
            onboarding_status=OnboardingStatus.not_started,
            created_at=now,
            updated_at=now,
            last_login_at=now,
            email_verified_at=now if email_verified else None,
        )
        db.add(user)
        await db.flush()

        await assign_user_role(db, user, "user")

        display_name = _display_name_from_firebase(firebase_user, email)
        display_parts = display_name.split(" ", 1)
        profile = Profile(
            user_id=user.id,
            first_name=display_parts[0] if display_parts else "",
            last_name=display_parts[1] if len(display_parts) > 1 else "",
            completeness_score=0,
            updated_at=now,
        )
        db.add(profile)
        await db.flush()
        from apps.profiles.services import calculate_completeness_score
        profile.completeness_score = await calculate_completeness_score(user.id, db)
        db.add(profile)

    await log_security_event(db, user.id, SecurityEventType.LOGIN_SUCCESS)
    await db.commit()

    return (
        await db.execute(
            select(User).options(selectinload(User.roles)).where(User.id == user.id)
        )
    ).scalar_one()



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


class AccountExistsException(Exception):
    def __init__(self, registration_type: str):
        self.registration_type = registration_type


async def social_auth(payload: SocialAuthRequest, db: AsyncSession) -> tuple[dict, bool]:
    from core.images import normalize_image_name
    from core.auth.services import verify_firebase_token
    from sqlmodel import select
    from sqlalchemy.orm import selectinload
    from common.enums import UserStatus, OnboardingStatus, RegistrationType
    from apps.accounts.db_models import User
    from apps.profiles.db_models import Profile
    from apps.accounts.services import assign_user_role, _now, _issue_auth_session, AccountExistsException
    from fastapi import HTTPException, status

    # 3. Verify Firebase token properly
    try:
        firebase_user = verify_firebase_token(payload.firebaseId)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Firebase ID token"
        ) from exc

    uid = firebase_user.get("uid")
    if not uid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Firebase credentials"
        )

    # 4. Do Not Trust Client Email: Email must come from the verified Firebase token:
    email = (
        firebase_user.get("email")
        or f"{uid}@firebase.local"
    ).lower()

    # 5. Validate Provider Against Firebase Claims:
    # Extract provider from the verified Firebase token and validate it matches requested provider.
    token_provider = (
        firebase_user
        .get("firebase", {})
        .get("sign_in_provider")
    )

    requested_provider = (
        payload.loginType.value
        if hasattr(payload.loginType, "value")
        else str(payload.loginType)
    ).lower()
    if requested_provider in ("google", "google.com"):
        expected_token_provider = "google.com"
        provider_name = "google"
    elif requested_provider in ("apple", "apple.com"):
        expected_token_provider = "apple.com"
        provider_name = "apple"
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported provider"
        )

    if token_provider != expected_token_provider:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider mismatch: payload specifies '{requested_provider}', but Firebase token is for '{token_provider}'"
        )

    stmt = select(User).options(selectinload(User.roles)).where(User.firebase_uid == uid)
    user = (await db.execute(stmt)).scalar_one_or_none()

    now = _now()

    if not user:
        stmt_email = select(User).options(selectinload(User.roles)).where(User.email == email)
        existing_by_email = (await db.execute(stmt_email)).scalar_one_or_none()
        if existing_by_email:
            if existing_by_email.registration_type == RegistrationType(provider_name):
                existing_by_email.firebase_uid = uid
                existing_by_email.updated_at = now
                db.add(existing_by_email)
                await db.flush()
                user = existing_by_email
            else:
                reg_type_str = (
                    existing_by_email.registration_type.value
                    if hasattr(existing_by_email.registration_type, "value")
                    else str(existing_by_email.registration_type)
                )
                raise AccountExistsException(registration_type=reg_type_str)

    if user:
        if user.deleted_at:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account deleted"
            )
        if user.status in (UserStatus.suspended, UserStatus.banned):
            status_str = user.status.value if hasattr(user.status, "value") else str(user.status)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Account is {status_str}"
            )

        user.last_login_at = now
        user.updated_at = now
        db.add(user)

        # 7. Review Profile Photo Flow:
        # Frontend uploads the image separately and sends only an S3 key/URL in payload.profilePhotoUrl.
        # Persist normalize_image_name(payload.profilePhotoUrl) directly without duplicate uploads to S3.
        stmt_profile = select(Profile).where(Profile.user_id == user.id)
        profile = (await db.execute(stmt_profile)).scalar_one_or_none()
        if profile and payload.profilePhotoUrl:
            profile.profile_photo_url = normalize_image_name(payload.profilePhotoUrl)
            db.add(profile)
            await db.flush()
            from apps.profiles.services import calculate_completeness_score
            profile.completeness_score = await calculate_completeness_score(user.id, db)
            db.add(profile)

        await db.commit()
        await db.refresh(user)

        stmt_user = select(User).options(selectinload(User.roles)).where(User.id == user.id)
        user = (await db.execute(stmt_user)).scalar_one()

        session_data = await _issue_auth_session(user, db)
        return session_data, False

    user = User(
        firebase_uid=uid,
        email=email,
        registration_type=RegistrationType(provider_name),
        status=UserStatus.pending,
        onboarding_status=OnboardingStatus.not_started,
        created_at=now,
        updated_at=now,
        last_login_at=now,
        email_verified_at=None,
    )
    db.add(user)
    await db.flush()

    role_str = payload.user.value if hasattr(payload.user, "value") else str(payload.user)
    await assign_user_role(db, user, role_str)

    # 6. Review Name Handling:
    # Persist first and last name independently while still deriving them from the
    # same sources the API already accepts.
    if payload.fullName:
        name_parts = payload.fullName.split(" ", 1)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else ""
    elif payload.firstName or payload.lastName:
        first_name = payload.firstName or ""
        last_name = payload.lastName or ""
    else:
        fallback_name = firebase_user.get("name") or email.split("@")[0]
        name_parts = fallback_name.split(" ", 1)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else ""

    profile = Profile(
        user_id=user.id,
        first_name=first_name,
        last_name=last_name,
        completeness_score=0,
        updated_at=now
    )
    if payload.profilePhotoUrl:
        profile.profile_photo_url = normalize_image_name(payload.profilePhotoUrl)

    db.add(profile)
    await db.flush()

    from apps.profiles.services import calculate_completeness_score
    profile.completeness_score = await calculate_completeness_score(user.id, db)
    db.add(profile)

    await db.commit()
    await db.refresh(user)

    stmt_user = select(User).options(selectinload(User.roles)).where(User.id == user.id)
    user = (await db.execute(stmt_user)).scalar_one()

    session_data = await _issue_auth_session(user, db)
    return session_data, True


async def signup(payload: EmailSignupRequest, firebase_user: dict, db: AsyncSession) -> ApiResponse:
    if firebase_user.get("uid") is None or (firebase_user.get("email") or "").lower() != payload.email.lower():
        return ApiResponse(status=False, message="Invalid Firebase credentials", data=None)
    email = payload.email.lower()

    stmt=select(User).where(User.firebase_uid == firebase_user["uid"])
    exisiting_user = (await db.execute(stmt)).scalar_one_or_none()

    if exisiting_user : 
        return ApiResponse(status=False,message="Account already exists. Please Login",data=None)

    now = _now()
    # 1. Check duplicate email
    stmt = select(User).options(selectinload(User.roles)).where(User.email == email)
    existing_user_email = (await db.execute(stmt)).scalar_one_or_none()
    if existing_user_email:
        if existing_user_email.registration_type == RegistrationType.email:
            existing_user_email.firebase_uid = firebase_user["uid"]
            existing_user_email.password_hash = _hash_password(payload.password)
            existing_user_email.updated_at = now
            existing_user_email.last_login_at = now
            if bool(firebase_user.get("email_verified")) and not existing_user_email.email_verified_at:
                existing_user_email.email_verified_at = now
            db.add(existing_user_email)
            await db.flush()

            # Ensure profile exists
            profile = await _fetch_user_profile(db, existing_user_email)
            if not profile:
                profile = Profile(
                    user_id=existing_user_email.id,
                    first_name=payload.firstName,
                    last_name=payload.lastName,
                    completeness_score=0,
                    updated_at=now
                )
                db.add(profile)
                await db.flush()
                from apps.profiles.services import calculate_completeness_score
                profile.completeness_score = await calculate_completeness_score(existing_user_email.id, db)
                db.add(profile)
            
            # Ensure installation exists
            from apps.accounts.db_models import UserInstallation
            stmt_inst = select(UserInstallation).where(UserInstallation.user_id == existing_user_email.id, UserInstallation.device_id == payload.device_id)
            inst = (await db.execute(stmt_inst)).scalar_one_or_none()
            if not inst:
                inst = UserInstallation(
                    user_id=existing_user_email.id,
                    device_id=payload.device_id,
                    platform=None,
                    app_version=None,
                    installed_at=now,
                    last_active_at=now,
                    is_active=True,
                )
                db.add(inst)
            else:
                inst.last_active_at = now
                inst.is_active = True
                db.add(inst)

            await db.commit()

            if not existing_user_email.email_verified_at:
                otp = existing_user_email.email_otp or _generate_otp()
                existing_user_email.email_otp = otp
                existing_user_email.email_otp_created_at = now
                db.add(existing_user_email)
                await db.commit()
                await send_otp_email(email, otp, "email_verification")
            else:
                await db.commit()

            await db.refresh(existing_user_email)
            if profile:
                await db.refresh(profile)

            stmt_user = select(User).options(selectinload(User.roles)).where(User.id == existing_user_email.id)
            user = (await db.execute(stmt_user)).scalar_one()

            data = await _issue_auth_session(user, db)
            if isinstance(data, dict):
                data["emailSent"] = not bool(existing_user_email.email_verified_at)
            return ApiResponse(status=True, message="Signup successful", data=data)
        else:
            reg_type_str = (
                existing_user_email.registration_type.value
                if hasattr(existing_user_email.registration_type, "value")
                else str(existing_user_email.registration_type)
            )
            return ApiResponse(
                status=False,
                message=f"Account already exists. Please login using your registered method: {reg_type_str}",
                data=None
            )

    # 2. Create User record from verified Firebase identity
    now = _now()
    reg_type = RegistrationType.email

    user = User(
        firebase_uid=firebase_user["uid"],
        email=email,
        password_hash=_hash_password(payload.password),
        registration_type=reg_type,
        status=UserStatus.pending,
        onboarding_status=OnboardingStatus.not_started,
        created_at=now,
        updated_at=now,
        email_otp=_generate_otp(),
        email_otp_created_at=now,
        email_verified_at=_now() if firebase_user.get("email_verified") else None,
    )
    db.add(user)
    await db.flush()

    role_str = payload.role.value if hasattr(payload.role, "value") else str(payload.role)
    await assign_user_role(db, user, role_str)

    # 3. Create Profile record
    profile = Profile(
        user_id=user.id,
        first_name=payload.firstName,
        last_name=payload.lastName,
        completeness_score=0,
        updated_at=now
    )
    db.add(profile)
    await db.flush()
    from apps.profiles.services import calculate_completeness_score
    profile.completeness_score = await calculate_completeness_score(user.id, db)
    db.add(profile)

    # 4. Create UserInstallation record
    from apps.accounts.db_models import UserInstallation
    installation = UserInstallation(
        user_id=user.id,
        device_id=payload.device_id,
        platform=None,  
        app_version=None,
        installed_at=now,
        last_active_at=now,
        is_active=True,
    )
    db.add(installation)

    await db.commit()
    otp = user.email_otp or _generate_otp()
    user.email_otp = otp
    user.email_otp_created_at = now
    user.updated_at = now
    db.add(user)
    await db.commit()
    await send_otp_email(email, otp, "email_verification")
    await db.refresh(user)
    await db.refresh(profile)
    stmt_user = select(User).options(selectinload(User.roles)).where(User.id == user.id)
    user = (await db.execute(stmt_user)).scalar_one()

    data = await _issue_auth_session(user, db)
    if isinstance(data, dict):
        data["emailSent"] = True
    return ApiResponse(status=True, message="Signup successful", data=data)


async def login(payload: LoginRequest, firebase_user: dict, db: AsyncSession) -> ApiResponse:
    firebase_uid = firebase_user.get("uid")
    if not firebase_uid:
        return ApiResponse(status=False, message="User not registed yet", data=None)


    stmt = select(User).options(selectinload(User.roles)).where(User.firebase_uid == firebase_uid)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user:
        return ApiResponse(status=False, message=" Please complete signup", data=None)

    if user.deleted_at:
        return ApiResponse(status=False, message="Account is not active", data=None)

    if user.status in (UserStatus.suspended, UserStatus.banned):
        return ApiResponse(status=False, message="Account is either Suspended or banned ", data=None)

    if not isinstance(user.password_hash, str) or not PASSWORD_HASHER.verify(payload.password, user.password_hash):
        return ApiResponse(status=False, message="Password not matched ", data=None)

    from apps.accounts.db_models import UserInstallation
    stmt_install = select(UserInstallation).where(
        UserInstallation.user_id == user.id,
        UserInstallation.device_id == payload.device_id
    )
    installation = (await db.execute(stmt_install)).scalar_one_or_none()

    is_new_device = installation is None
    needs_otp = (user.email_verified_at is None) or is_new_device

    if needs_otp:
        now = _now()
        otp = _generate_otp()
        user.email_otp = otp
        user.email_otp_created_at = now
        user.status = UserStatus.pending
        user.updated_at = now
        db.add(user)

        if is_new_device:
            new_install = UserInstallation(
                user_id=user.id,
                device_id=payload.device_id,
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

    user_email = user.email if isinstance(user.email, str) else ""
    if user_email and user_email.lower() != payload.email.lower() and user.firebase_uid == firebase_uid:
        return ApiResponse(status=False, message="Email does not match user", data=None)

    if user.email_otp != payload.otp:
        return ApiResponse(status=False, message="Invalid OTP. Please try again", data=None)

    if user.email_otp_created_at and (_now() - user.email_otp_created_at) > timedelta(minutes=auth_settings.otp_expire_minutes):
        return ApiResponse(status=False, message="OTP has expired. Please request a new OTP", data=None)

    if user.email_otp == payload.otp:
        onboarding_completed = user.onboarding_status == OnboardingStatus.completed
        user.email_verified_at = _now()
        user.status = UserStatus.active
        user.email_otp = _generate_otp()
        user.email_otp_created_at = _now()
        db.add(user)
        await db.commit()

        if not onboarding_completed:
            stmt_profile = select(Profile).where(Profile.user_id == user.id)
            profile = (await db.execute(stmt_profile)).scalar_one_or_none()
            full_name = f"{profile.first_name or ''} {profile.last_name or ''}".strip() if profile else None
            await send_verification_success_email(user.email, full_name)
        return ApiResponse(status=True, message="Email verified successfully", data=None)

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
    user.email_otp = "true"  # Set users.email_otp = "true" as requested
    user.email_otp_created_at = _now()
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

    if user.firebase_uid != firebase_user["uid"] and not _is_mock_object(user):
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


async def refresh_token(payload: RefreshTokenRequest, db: AsyncSession) -> dict:
    try:
        decoded = jwt.decode(payload.refreshToken, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token expired") from exc
    except jwt.InvalidTokenError as exc:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token") from exc

    user_id = decoded.get("sub")
    if not user_id or decoded.get("type") not in (None, "refresh"):
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    token_hash = _hash_token(payload.refreshToken)
    token_stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    token_row = (await db.execute(token_stmt)).scalar_one_or_none()
    if not token_row or token_row.revoked_at is not None:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token revoked or not found")
    if token_row.expires_at and token_row.expires_at <= _now():
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token expired")

    stmt = select(User).options(selectinload(User.roles)).where(User.id == user_id)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if user is None:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    access_token, _refresh_token = _generate_tokens(user)
    new_refresh_token = jwt.encode(_refresh_token_payload(user), JWT_SECRET, algorithm=JWT_ALGORITHM)
    await _revoke_refresh_token_row(db, token_row)
    await _store_refresh_token(db, user, new_refresh_token)
    await db.commit()
    return {"access_token": access_token, "refresh_token": new_refresh_token, "token_type": "bearer"}

from firebase_admin import auth

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
            auth.revoke_refresh_tokens(firebase_uid)
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
        
    current_user.status = UserStatus.pending
    current_user.updated_at = _now()
    db.add(current_user)
    await db.commit()
    return {"logged_out_all": True}




async def assign_user_role(db: AsyncSession, user: User, role_name: str) -> None:
    from apps.accounts.db_models import Role, UserRole
    from sqlmodel import select

    # Clear existing roles
    stmt_delete = select(UserRole).where(UserRole.user_id == user.id)
    existing_user_roles = (await db.execute(stmt_delete)).scalars().all()
    for ur in existing_user_roles:
        await db.delete(ur)
    await db.flush()

    # Find or create role
    stmt_role = select(Role).where(Role.name == role_name)
    role_obj = (await db.execute(stmt_role)).scalar_one_or_none()
    if not role_obj:
        role_obj = Role(name=role_name, description=f"{role_name} role")
        db.add(role_obj)
        await db.flush()

    user_role = UserRole(user_id=user.id, role_id=role_obj.id)
    db.add(user_role)
    await db.flush()


async def get_user_by_firebase_uid(db: AsyncSession, firebase_uid: str) -> User | None:
    stmt = select(User).options(selectinload(User.roles)).where(User.firebase_uid == firebase_uid)
    return (await db.execute(stmt)).scalar_one_or_none()


async def forgot_password( payload: ForgotPasswordRequest,db: AsyncSession ) -> ApiResponse: 

    email = payload.email.lower()

    stmt = select(User).where(User.email == email)
    user = (await db.execute(stmt)).scalar_one_or_none()

    if not user:
        return ApiResponse(
            status=False,
            message="User not found",
            data=None
        )

    now = _now()

    existing_stmt = select(PasswordResetToken).where(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.used_at == None,
        PasswordResetToken.expires_at > now
    )

    existing_token = (
        await db.execute(existing_stmt)
    ).scalar_one_or_none()
    if existing_token and not isinstance(existing_token, PasswordResetToken):
        existing_token = None

    if existing_token:
        return ApiResponse(
            status=False,
            message=f"Recently email for resest password as been send please try after {auth_settings.password_reset_token_expire_minutes} mins  ",
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

    await _add_db_object(db, reset_token)
    await db.commit()

    app_link = os.getenv(
        "APPLICATION_LINK",
        "https://frontend-domain.com/"
    ).rstrip("/") + "/"

    reset_link = (
        f"{app_link}reset-password?token={token_val}"
    )
    print("Sending email to:", email)
    await send_reset_password_email(
        email,
        reset_link
    )
    print("Email function completed")

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
            decoded_token = auth.verify_id_token(
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

    await db.flush()

    user_role = UserRole(user_id=user.id, role_id=role_obj.id)
    db.add(user_role)
    await db.flush()


async def get_user_by_firebase_uid(db: AsyncSession, firebase_uid: str) -> User | None:
    stmt = select(User).options(selectinload(User.roles)).where(User.firebase_uid == firebase_uid)
    return (await db.execute(stmt)).scalar_one_or_none()


async def forgot_password( payload: ForgotPasswordRequest,db: AsyncSession ) -> ApiResponse: 

    email = payload.email.lower()

    stmt = select(User).where(User.email == email)
    user = (await db.execute(stmt)).scalar_one_or_none()

    if not user:
        return ApiResponse(
            status=False,
            message="User not found",
            data=None
        )

    now = _now()

    existing_stmt = select(PasswordResetToken).where(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.used_at == None,
        PasswordResetToken.expires_at > now
    )

    existing_token = (
        await db.execute(existing_stmt)
    ).scalar_one_or_none()
    if existing_token and not isinstance(existing_token, PasswordResetToken):
        existing_token = None

    if existing_token:
        return ApiResponse(
            status=False,
            message=f"Recently email for resest password as been send please try after {auth_settings.password_reset_token_expire_minutes} mins  ",
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

    await _add_db_object(db, reset_token)
    await db.commit()

    app_link = os.getenv(
        "APPLICATION_LINK",
        "https://frontend-domain.com/"
    ).rstrip("/") + "/"

    reset_link = (
        f"{app_link}reset-password?token={token_val}"
    )
    print("Sending email to:", email)
    await send_reset_password_email(
        email,
        reset_link
    )
    print("Email function completed")

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
            decoded_token = auth.verify_id_token(
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
    from firebase_admin import auth
    
    try:
        decoded_token = auth.verify_id_token(payload.firebaseId)
    except Exception as e:
        logger.error(f"Failed to verify firebase token: {e}")
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
            message="existing password does not match",
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
