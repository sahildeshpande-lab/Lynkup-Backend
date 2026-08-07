from __future__ import annotations
import logging
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from sqlalchemy.orm import selectinload
from apps.accounts.db_models import SecurityEventType, User
from apps.profiles.db_models import Profile
from common.enums import OnboardingStatus, RegistrationType, UserStatus, inactive_account_message
from common.exceptions import ApiError
from core.auth.config import settings as auth_settings
from ..schemas import ApiResponse, EmailSignupRequest, SocialAuthRequest
from core.auth.services import verify_firebase_token
LOGIN_EVENT_THROTTLE_SECONDS = auth_settings.login_event_throttle_seconds

from .auth_service import _issue_auth_session
from .common_service import AccountExistsException, _as_aware_utc, _display_name_from_firebase, _fetch_user_profile, _hash_password, _now, _registration_type_from_firebase, assign_user_role, log_security_event
from .device_otp_service import (
    attach_otp_flags,
    begin_otp_challenge,
    ensure_unverified_installation,
    evaluate_device_otp_requirement,
    upsert_user_installation,
)

logger = logging.getLogger(__name__)

async def complete_firebase_registration(firebase_user: dict, db: AsyncSession) -> User:
    firebase_uid = firebase_user["uid"]
    stmt = select(User).options(selectinload(User.roles)).where(User.firebase_uid == firebase_uid)
    user = (await db.execute(stmt)).scalar_one_or_none()
    now = _now()

    if user:
        if user.status == UserStatus.deleting or user.deleted_at:
            raise ApiError(inactive_account_message(UserStatus.deleting))
        if user.status in (UserStatus.suspended, UserStatus.banned):
            raise ApiError(inactive_account_message(user.status))

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

    registration_type = _registration_type_from_firebase(firebase_user)

    if existing_user:
        # Link account if it has no firebase_uid, or if firebase_uid differs but registration type matches (recreated Firebase account)
        if (not existing_user.firebase_uid) or (existing_user.firebase_uid != firebase_uid and existing_user.registration_type == registration_type):
            existing_user.firebase_uid = firebase_uid
            existing_user.updated_at = now
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
            status=UserStatus.pending,
            onboarding_status=OnboardingStatus.not_started,
            created_at=now,
            updated_at=now,
            last_login_at=now,
            email_verified_at=None,
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

async def _refresh_user_topic_subscriptions_best_effort(
    db: AsyncSession,
    user: User,
    *,
    context: str,
) -> None:
    try:
        from apps.notifications.services.topic_service import TopicService

        profile = await _fetch_user_profile(db, user)
        if profile is not None:
            await TopicService.refresh_user_topic_subscriptions(db, user.id, profile)
    except Exception:
        logger.exception(
            "Firebase topic sync failed during social auth (%s) user_id=%s",
            context,
            user.id,
        )


async def _build_device_auth_session(
    db: AsyncSession,
    user: User,
    device_id: str,
    *,
    platform: str | None = None,
    fcm_token: str | None = None,
) -> tuple[dict, str]:
    from sqlalchemy.orm import selectinload

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
            platform=platform,
            fcm_token=fcm_token,
        )
        await _refresh_user_topic_subscriptions_best_effort(db, user, context="otp flow")
        stmt_user = select(User).options(selectinload(User.roles)).where(User.id == user.id)
        user = (await db.execute(stmt_user)).scalar_one()
        message = (
            "Verification email sent. Please verify your OTP."
            if email_sent
            else "Please verify your OTP."
        )
        return (
            attach_otp_flags(
                await _issue_auth_session(user, db),
                email_sent=email_sent,
                needs_otp=True,
            ),
            message,
        )

    user.status = UserStatus.active
    user.updated_at = _now()
    db.add(user)

    await upsert_user_installation(
        db,
        user.id,
        device_id,
        platform=platform,
        fcm_token=fcm_token,
        now=_now(),
    )

    await db.commit()
    await _refresh_user_topic_subscriptions_best_effort(db, user, context="no-otp flow")
    stmt_user = select(User).options(selectinload(User.roles)).where(User.id == user.id)
    user = (await db.execute(stmt_user)).scalar_one()
    return (
        attach_otp_flags(
            await _issue_auth_session(user, db),
            email_sent=False,
            needs_otp=False,
        ),
        "Login successful",
    )


async def social_auth(payload: SocialAuthRequest, db: AsyncSession) -> tuple[dict, bool, str]:
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
        if user.status == UserStatus.deleting or user.deleted_at:
            raise HTTPException(
                status_code=status.HTTP_200_OK,
                detail=inactive_account_message(UserStatus.deleting)
            )
        if user.status in (UserStatus.suspended, UserStatus.banned):
            raise HTTPException(
                status_code=status.HTTP_200_OK,
                detail=inactive_account_message(user.status)
            )

        user.last_login_at = now
        user.updated_at = now
        db.add(user)

        stmt_profile = select(Profile).where(Profile.user_id == user.id)
        profile = (await db.execute(stmt_profile)).scalar_one_or_none()
        if profile and payload.profile_photo_url:
            # Store Google/Apple photo URL as-is (no S3 key normalization).
            profile.profile_photo_url = payload.profile_photo_url
            db.add(profile)
            await db.flush()
            from apps.profiles.services import calculate_completeness_score
            profile.completeness_score = await calculate_completeness_score(user.id, db)
            db.add(profile)

        await db.flush()
        session_data, message = await _build_device_auth_session(
            db,
            user,
            payload.device_id,
            platform=payload.platform,
            fcm_token=payload.fcm_token,
        )
        return session_data, False, message

    user = User(
        firebase_uid=uid,
        email=email,
        password_hash=None,  # Social auth never stores a password
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
        # Store Google/Apple photo URL as-is when provided.
        profile_photo_url=payload.profile_photo_url,
        completeness_score=0,
        updated_at=now
    )
    db.add(profile)
    await db.flush()

    from apps.profiles.services import calculate_completeness_score
    profile.completeness_score = await calculate_completeness_score(user.id, db)
    db.add(profile)

    await db.commit()
    await db.refresh(user)

    stmt_user = select(User).options(selectinload(User.roles)).where(User.id == user.id)
    user = (await db.execute(stmt_user)).scalar_one()

    session_data, message = await _build_device_auth_session(
        db,
        user,
        payload.device_id,
        platform=payload.platform,
        fcm_token=payload.fcm_token,
    )
    return session_data, True, message

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
            password_hash = _hash_password(payload.password)
            if not password_hash:
                return ApiResponse(status=False, message="Password is required", data=None)
            existing_user_email.firebase_uid = firebase_user["uid"]
            existing_user_email.password_hash = password_hash
            existing_user_email.updated_at = now
            existing_user_email.last_login_at = now
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

            # Ensure unverified installation exists (never mark trusted here).
            await ensure_unverified_installation(
                db,
                existing_user_email.id,
                payload.device_id,
                platform=payload.platform,
                fcm_token=payload.fcm_token,
                now=now,
            )
            await db.commit()

            email_sent = await begin_otp_challenge(
                db,
                existing_user_email,
                payload.device_id,
                platform=payload.platform,
                fcm_token=payload.fcm_token,
            )

            await db.refresh(existing_user_email)
            if profile:
                await db.refresh(profile)

            stmt_user = select(User).options(selectinload(User.roles)).where(User.id == existing_user_email.id)
            user = (await db.execute(stmt_user)).scalar_one()

            data = attach_otp_flags(
                await _issue_auth_session(user, db),
                email_sent=email_sent,
                needs_otp=True,
            )
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
    password_hash = _hash_password(payload.password)
    if not password_hash:
        return ApiResponse(status=False, message="Password is required", data=None)

    user = User(
        firebase_uid=firebase_user["uid"],
        email=email,
        password_hash=password_hash,
        registration_type=reg_type,
        status=UserStatus.pending,
        onboarding_status=OnboardingStatus.not_started,
        created_at=now,
        updated_at=now,
        email_otp=None,
        email_otp_created_at=None,
        email_verified_at=None,
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

    # 4. Create unverified installation — never mark trusted during signup.
    await ensure_unverified_installation(
        db,
        user.id,
        payload.device_id,
        platform=payload.platform,
        fcm_token=payload.fcm_token,
        now=now,
    )
    await db.commit()

    email_sent = await begin_otp_challenge(
        db,
        user,
        payload.device_id,
        is_new_device=True,
        platform=payload.platform,
        fcm_token=payload.fcm_token,
    )
    await db.refresh(user)
    await db.refresh(profile)
    stmt_user = select(User).options(selectinload(User.roles)).where(User.id == user.id)
    user = (await db.execute(stmt_user)).scalar_one()

    data = attach_otp_flags(
        await _issue_auth_session(user, db),
        email_sent=email_sent,
        needs_otp=True,
    )
    return ApiResponse(status=True, message="Signup successful", data=data)
