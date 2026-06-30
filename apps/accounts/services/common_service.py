from __future__ import annotations
import hashlib
import os
import secrets
import jwt
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from sqlalchemy.orm import selectinload
from pwdlib import PasswordHash
from pwdlib.hashers.bcrypt import BcryptHasher
from apps.accounts.db_models import Role, UserRole
from apps.accounts.db_models import RefreshToken, SecurityEvent, SecurityEventType, TransactionalEmailLog, User
from apps.profiles.db_models import Profile
from common.enums import RegistrationType
from core.auth.config import settings as auth_settings
from ..schemas import AuthUserResponse
JWT_SECRET = auth_settings.jwt_secret
JWT_ALGORITHM = auth_settings.jwt_algorithm
ACCESS_TOKEN_EXPIRE_MINUTES = auth_settings.access_token_expire_minutes
PASSWORD_HASHER = PasswordHash((BcryptHasher(),))

def _now() -> datetime:
    return datetime.now(timezone.utc)

async def _fetch_user_profile(db: AsyncSession, user: User) -> Profile | None:
    stmt = select(Profile).where(Profile.user_id == user.id)
    return (await db.execute(stmt)).scalar_one_or_none()

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
        profilePhoto_url=generate_download_url(profile.profile_photo_url) if (profile and profile.profile_photo_url) else None,
        bannerPhotoUrl=generate_download_url(profile.banner_photo_url) if (profile and profile.banner_photo_url) else None,
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

class AccountExistsException(Exception):
    def __init__(self, registration_type: str):
        self.registration_type = registration_type

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
