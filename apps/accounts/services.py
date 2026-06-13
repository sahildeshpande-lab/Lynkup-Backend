from __future__ import annotations

import hashlib
import os
import secrets
import jwt
from datetime import datetime, timezone, timedelta
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import HTTPException, UploadFile, status
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from sqlalchemy.orm import selectinload
from pwdlib import PasswordHash

from apps.accounts.db_models import RefreshToken, SecurityEvent, SecurityEventType, TransactionalEmailLog, User, UserIdentity, PasswordResetToken
from apps.profiles.db_models import Profile
from common.enums import OnboardingStatus, RegistrationType, UserStatus
from core.auth.config import settings as auth_settings
from core.email_service import send_otp_email, build_email_verified_success_html

from .schemas import (
    # AdminSigninRequest,
    # AdminSignupRequest,
    # AdminEducationRequest,
    # AdminUserCreateRequest,
    # AdminUserUpdateRequest,
    ApiResponse,
    AuthSessionResponse,
    AuthUserResponse,
    EmailLoginRequest,
    EmailSignupRequest,
    LogoutRequest,
    RefreshTokenRequest,
    ResendOtpRequest,
    SocialAuthRequest,
    OtpVerifyRequest,
    RefreshSessionResponse,
    UserBaseResponse,
    ForgotPasswordRequest,
    ResetPasswordRequest,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


load_dotenv(Path(__file__).resolve().parents[2] / ".env")

JWT_SECRET = auth_settings.jwt_secret
JWT_ALGORITHM = auth_settings.jwt_algorithm
ACCESS_TOKEN_EXPIRE_MINUTES = auth_settings.access_token_expire_minutes
REFRESH_TOKEN_EXPIRE_DAYS = auth_settings.refresh_token_expire_days
LOGIN_EVENT_THROTTLE_SECONDS = auth_settings.login_event_throttle_seconds
ARGON2_HASHER = PasswordHash.recommended()


def _hash_password(password: str, username: str | None = None) -> str:
    return ARGON2_HASHER.hash(password)


def _generate_otp() -> str:
    return f"{secrets.randbelow(900000) + 100000}"


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
    refresh_expiry = now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)

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
        "exp": int(refresh_expiry.timestamp()),
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
        expires_at=now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
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
        "exp": int((now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)).timestamp()),
        "iat": int(now.timestamp()),
    }


def _build_auth_user_response(user: User, profile: Profile | None) -> AuthUserResponse:
    display_name = profile.display_name if profile else ""
    first_name = ""
    last_name = ""
    if display_name:
        parts = display_name.split(" ", 1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else ""

    return AuthUserResponse(
        id=str(user.id),
        firebase_uid=user.firebase_uid,
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
    )


def _registration_type_from_firebase(firebase_user: dict) -> RegistrationType:
    provider = (
        firebase_user.get("firebase", {})
        .get("sign_in_provider", "password")
    )
    if provider == "google.com":
        return RegistrationType.Google
    if provider == "apple.com":
        return RegistrationType.Apple
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
    email_verified = bool(firebase_user.get("email_verified"))
    display_name = _display_name_from_firebase(firebase_user, email)
    registration_type = _registration_type_from_firebase(firebase_user)
    provider = firebase_user.get("firebase", {}).get("sign_in_provider", "firebase")

    user = User(
        firebase_uid=firebase_user["uid"],
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

    identity = UserIdentity(
        user_id=user.id,
        provider=provider,
        provider_uid=firebase_uid[:128],
        linked_at=now,
    )
    db.add(identity)

    profile = Profile(
        user_id=user.id,
        display_name=display_name,
        completeness_score=33,
        updated_at=now,
    )
    db.add(profile)
    await log_security_event(db, user.id, SecurityEventType.LOGIN_SUCCESS)
    await db.commit()

    return (
        await db.execute(
            select(User).options(selectinload(User.roles)).where(User.id == user.id)
        )
    ).scalar_one()


async def _fetch_user_profile(db: AsyncSession, user: User) -> Profile | None:
    return (await db.execute(select(Profile).where(Profile.user_id == user.id))).scalar_one_or_none()


async def _issue_auth_session(user: User, db: AsyncSession) -> dict:
    profile = await _fetch_user_profile(db, user)
    auth_user = _build_auth_user_response(user, profile)
    access_token, refresh_token = _generate_tokens(user)
    await _store_refresh_token(db, user, refresh_token)
    await db.commit()
    return AuthSessionResponse(
        user=auth_user,
        emailSent=False,
        access_token=access_token,
        refresh_token=refresh_token,
    ).model_dump()


async def build_firebase_session_response(user: User, db: AsyncSession) -> dict:
    profile = await _fetch_user_profile(db, user)
    return {
        "user": _build_auth_user_response(user, profile).model_dump(),
        "token_type": "firebase",
    }


async def social_auth(payload: SocialAuthRequest, db: AsyncSession) -> dict:
    from core.images import upload_image_to_s3, normalize_image_name
    from core.auth.services import verify_firebase_token
    from sqlmodel import select

    try:
        firebase_user = verify_firebase_token(payload.idToken, check_revoked=False)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Firebase ID token") from exc

    firebase_uid = firebase_user["uid"]
    email = (firebase_user.get("email") or payload.email or f"{firebase_uid}@firebase.local").lower()
    stmt = select(User).options(selectinload(User.roles)).where(User.email == email)
    user = (await db.execute(stmt)).scalar_one_or_none()
    
    now = _now()
    if not user:
        user = User(
            firebase_uid=firebase_uid,
            email=email,
            status=UserStatus.active,
            onboarding_status=OnboardingStatus.not_started,
            created_at=now,
            updated_at=now,
            email_verified_at=now if firebase_user.get("email_verified") else None,
        )
        db.add(user)
        await db.flush()

        await assign_user_role(db, user, "user")
        
        identity = UserIdentity(
            user_id=user.id,
            provider=payload.provider.value if hasattr(payload.provider, 'value') else str(payload.provider),
            provider_uid=firebase_uid,
            linked_at=now
        )
        db.add(identity)
        
        profile = Profile(
            user_id=user.id,
            display_name=payload.fullName or f"{payload.firstName or ''} {payload.lastName or ''}".strip() or email.split("@")[0],
            completeness_score=33,
            updated_at=now
        )
        db.add(profile)
    else:
        stmt_profile = select(Profile).where(Profile.user_id == user.id)
        profile = (await db.execute(stmt_profile)).scalar_one_or_none()
        if not profile:
            profile = Profile(
                user_id=user.id,
                display_name=f"{payload.firstName or ''} {payload.lastName or ''}".strip() or user.email.split("@")[0],
                completeness_score=33,
                updated_at=now
            )
            db.add(profile)
            
    if payload.profilePhotoUrl:
        uploaded_url = await upload_image_to_s3(payload.profilePhotoUrl, prefix="profiles")
        profile.profile_photo_url = normalize_image_name(uploaded_url)
        db.add(profile)
        
    await db.commit()
    await db.refresh(user)
    await db.refresh(profile)
    
    return await _issue_auth_session(user, db)


async def social_auth_form(
    provider: SocialProvider,
    idToken: str,
    email: str | None,
    firstName: str | None,
    lastName: str | None,
    fullName: str | None,
    profilePhoto: UploadFile | None,
    db: AsyncSession,
) -> dict:
    from core.images import s3_client, settings, generate_download_url, normalize_image_name
    from core.auth.services import verify_firebase_token
    from sqlmodel import select
    import uuid

    try:
        firebase_user = verify_firebase_token(idToken, check_revoked=False)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Firebase ID token") from exc

    firebase_uid = firebase_user["uid"]
    email_val = (firebase_user.get("email") or email or f"{firebase_uid}@firebase.local").lower()
    stmt = select(User).where(User.email == email_val)
    user = (await db.execute(stmt)).scalar_one_or_none()
    
    now = _now()
    if not user:
        user = User(
            firebase_uid=firebase_uid,
            email=email_val,
            status=UserStatus.active,
            onboarding_status=OnboardingStatus.not_started,
            created_at=now,
            updated_at=now,
            email_verified_at=now if firebase_user.get("email_verified") else None,
        )
        db.add(user)
        await db.flush()

        await assign_user_role(db, user, "user")
        
        identity = UserIdentity(
            user_id=user.id,
            provider=provider.value if hasattr(provider, 'value') else str(provider),
            provider_uid=firebase_uid,
            linked_at=now
        )
        db.add(identity)
        
        profile = Profile(
            user_id=user.id,
            display_name=fullName or f"{firstName or ''} {lastName or ''}".strip() or email_val.split("@")[0],
            completeness_score=33,
            updated_at=now
        )
        db.add(profile)
    else:
        stmt_profile = select(Profile).where(Profile.user_id == user.id)
        profile = (await db.execute(stmt_profile)).scalar_one_or_none()
        if not profile:
            profile = Profile(
                user_id=user.id,
                display_name=f"{firstName or ''} {lastName or ''}".strip() or user.email.split("@")[0],
                completeness_score=33,
                updated_at=now
            )
            db.add(profile)
            
    if profilePhoto is not None and profilePhoto.filename:
        content = await profilePhoto.read()
        if content:
            ext = profilePhoto.filename.split(".")[-1] if "." in profilePhoto.filename else "png"
            file_name = f"profiles/{uuid.uuid4()}.{ext}"
            s3_client.put_object(
                Bucket=settings.aws_s3_bucket,
                Key=file_name,
                Body=content,
                ContentType=profilePhoto.content_type or "image/png"
            )
            profile.profile_photo_url = normalize_image_name(file_name)
            db.add(profile)
        
    await db.commit()
    await db.refresh(user)
    await db.refresh(profile)
    # Load roles eagerly to avoid MissingGreenlet when accessing user.role
    stmt_user = select(User).options(selectinload(User.roles)).where(User.id == user.id)
    user = (await db.execute(stmt_user)).scalar_one()
    return await _issue_auth_session(user, db)


async def signup(payload: EmailSignupRequest, firebase_user: dict, db: AsyncSession) -> ApiResponse:
    if firebase_user.get("uid") is None or (firebase_user.get("email") or "").lower() != payload.email.lower():
        return ApiResponse(status=False, message="Invalid Firebase credentials", data=None)
    email = payload.email.lower()

    # 1. Check duplicate email
    stmt = select(User).where(User.email == email)
    existing_user_email = (await db.execute(stmt)).scalar_one_or_none()
    if existing_user_email:
        return ApiResponse(status=False, message="Email already registered", data=None)

    # 2. Create User record from verified Firebase identity
    now = _now()
    reg_type = RegistrationType.email

    user = User(
        firebase_uid=f"firebase_{email.split('@')[0]}",
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
        display_name=f"{payload.firstName} {payload.lastName}".strip(),
        completeness_score=33,
        updated_at=now
    )
    db.add(profile)

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
    # Load roles eagerly to avoid MissingGreenlet when accessing user.role
    stmt_user = select(User).options(selectinload(User.roles)).where(User.id == user.id)
    user = (await db.execute(stmt_user)).scalar_one()

    data = await _issue_auth_session(user, db)
    if isinstance(data, dict):
        data["emailSent"] = True
    return ApiResponse(status=True, message="Signup successful", data=data)


async def login(firebase_user: dict, db: AsyncSession) -> ApiResponse:
    firebase_uid = firebase_user.get("uid")
    if not firebase_uid:
        return ApiResponse(status=False, message="Invalid Firebase credentials", data=None)

    stmt = select(User).options(selectinload(User.roles)).where(User.firebase_uid == firebase_uid)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user:
        return ApiResponse(status=False, message="User not found in local DB. Please sign up.", data=None)

    if user.deleted_at:
        return ApiResponse(status=False, message="Account is not active", data=None)

    if user.status == UserStatus.pending:
        now = _now()
        otp = _generate_otp()
        user.email_otp = otp
        user.email_otp_created_at = now
        user.updated_at = now
        db.add(user)
        await db.commit()
        await send_otp_email(user.email, otp, "email_verification")
        
        # Load roles eagerly to avoid MissingGreenlet when accessing user.role
        stmt_user = select(User).options(selectinload(User.roles)).where(User.id == user.id)
        user = (await db.execute(stmt_user)).scalar_one()

        data = await _issue_auth_session(user, db)
        if isinstance(data, dict):
            data["emailSent"] = True
        return ApiResponse(status=True, message="Verification email sent. Please verify your OTP.", data=data)

    if user.status != UserStatus.active:
        return ApiResponse(status=False, message="Account is not active", data=None)

    return ApiResponse(status=True, message="Login successful", data=await _issue_auth_session(user, db))


async def verify_otp(payload: OtpVerifyRequest, firebase_user: dict, db: AsyncSession):
    firebase_uid = firebase_user["uid"]
    stmt = select(User).where(User.firebase_uid == firebase_uid)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user:
        return ApiResponse(status=False, message="User not found", data=None)

    if user.email_otp != payload.otp:
        return ApiResponse(status=False, message="Invalid OTP", data=None)

    if user.email_otp_created_at and (_now() - user.email_otp_created_at) > timedelta(minutes=10):
        return ApiResponse(status=False, message="OTP expired", data=None)

    if user.email_otp == payload.otp:
        user.email_verified_at = _now()
        user.status = UserStatus.active
        user.email_otp = _generate_otp()
        user.email_otp_created_at = _now()
        db.add(user)
        await db.commit()

        stmt_profile = select(Profile).where(Profile.user_id == user.id)
        profile = (await db.execute(stmt_profile)).scalar_one_or_none()
        full_name = profile.display_name if profile else None

        html_content = build_email_verified_success_html(full_name)
        return HTMLResponse(content=html_content, status_code=200)

    return ApiResponse(status=False, message="Email not verified in Firebase yet", data=None)


async def resend_otp(payload: ResendOtpRequest, firebase_user: dict, db: AsyncSession) -> ApiResponse:
    stmt = select(User).where(User.email == payload.email.lower())
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user:
        return ApiResponse(status=False, message="User not found", data=None)

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
        firebase_uid=f"firebase_{primary_email.split('@')[0]}",
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
    if token_row.expires_at <= _now():
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


async def logout(payload: LogoutRequest, db: AsyncSession, current_user: User) -> dict:
    if payload.refreshToken:
        token_hash = _hash_token(payload.refreshToken)
        token_row = (
            await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash, RefreshToken.user_id == current_user.id))
        ).scalar_one_or_none()
        if token_row and token_row.revoked_at is None:
            await _revoke_refresh_token_row(db, token_row)
    
    current_user.status = UserStatus.pending
    current_user.updated_at = _now()
    db.add(current_user)
    await db.commit()
    return {"logged_out": True}


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


async def forgot_password(payload: ForgotPasswordRequest, db: AsyncSession) -> ApiResponse:
    from core.email_service import send_reset_password_email
    import uuid

    email = payload.email.lower()
    stmt = select(User).where(User.email == email)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user:
        return ApiResponse(status=False, message="User not found", data=None)

    # Check for recent active token to rate limit
    now = _now()
    existing_stmt = select(PasswordResetToken).where(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.used_at == None,
        PasswordResetToken.expires_at > now
    )
    existing_token = (await db.execute(existing_stmt)).scalar_one_or_none()
    if existing_token:
        return ApiResponse(
            status=False,
            message="Recently email for resest password as been send please try after 15 mins  ",
            data=None
        )

    # Generate token
    token_val = str(uuid.uuid4())
    now = _now()
    expires_at = now + timedelta(minutes=15)

    reset_token = PasswordResetToken(
        user_id=user.id,
        token=token_val,
        expires_at=expires_at,
    )
    db.add(reset_token)
    await db.commit()

    # Get application link
    app_link = os.getenv("APPLICATION_LINK", "https://frontend-domain.com/").rstrip("/") + "/"
    reset_link = f"{app_link}reset-password?token={token_val}"

    # Send email
    await send_reset_password_email(email, reset_link)

    return ApiResponse(status=True, message="Password reset link sent successfully", data=None)


async def reset_password(payload: ResetPasswordRequest, db: AsyncSession) -> ApiResponse:
    from firebase_admin import auth
    from core.auth.services import revoke_firebase_tokens
    import uuid

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
        return ApiResponse(status=False, message="Invalid or expired token", data=None)

    now = _now()
    if reset_token.expires_at.replace(tzinfo=timezone.utc) < now:
        return ApiResponse(status=False, message="Token expired", data=None)

    user = await db.get(User, reset_token.user_id)
    if not user:
        return ApiResponse(status=False, message="User not found", data=None)

    # Update password in Firebase
    try:
        auth.update_user(user.firebase_uid, password=payload.new_password)
    except Exception as exc:
        return ApiResponse(status=False, message=f"Failed to reset password: {str(exc)}", data=None)

    user.password_hash = _hash_password(payload.new_password)
    user.updated_at = now
    db.add(user)

    # Invalidate token
    reset_token.used_at = now
    db.add(reset_token)
    await db.commit()

    # Revoke tokens
    try:
        revoke_firebase_tokens(user.firebase_uid)
    except Exception:
        pass

    return ApiResponse(status=True, message="Password reset successful", data=None)
