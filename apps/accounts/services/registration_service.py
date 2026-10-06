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
from .common_service import (
    AccountExistsException,
    PUBLIC_AUTH_ACCOUNT_EXISTS_MESSAGE,
    SIGNUP_GENERIC_FAILURE_MESSAGE,
    STAFF_PUBLIC_AUTH_NOT_ALLOWED_MESSAGE,
    _as_aware_utc,
    _display_name_from_firebase,
    _existing_user_has_staff_role,
    _fetch_user_profile,
    firebase_email_matches_user,
    SOCIAL_EMAIL_MISMATCH_MESSAGE,
    _hash_password,
    ensure_public_signup_role,
    user_has_staff_role,
    _now,
    _registration_type_from_firebase,
    assign_user_role,
    is_soft_deleted_user,
    log_security_event,
    reactivate_soft_deleted_user,
)
from apps.user_deletion.services.account_recovery_service import (
    is_purge_window_expired,
    remove_expired_deleting_user_for_resignup,
)
from core.auth.services import delete_firebase_user_safely
from .consent_service import CONSENT_SOURCE_SIGNUP, save_current_consent
from .device_otp_service import (
    attach_otp_flags,
    begin_otp_challenge,
    ensure_unverified_installation,
    evaluate_device_otp_requirement,
    upsert_user_installation,
)

logger = logging.getLogger(__name__)


def _is_stored_profile_photo(value: str | None) -> bool:
    """True when the profile already has an S3/local key (not a remote provider URL)."""
    if not value or not str(value).strip():
        return False
    stripped = str(value).strip()
    return not stripped.startswith(("http://", "https://"))


async def _store_social_profile_photo(
    *,
    firebase_user: dict,
    payload: SocialAuthRequest,
    existing_photo_url: str | None,
) -> str | None:
    """Copy the Google/Apple avatar to S3 once; never replace an uploaded key."""
    if _is_stored_profile_photo(existing_photo_url):
        return str(existing_photo_url).strip()

    source = (
        (firebase_user.get("picture") or "").strip()
        or (payload.profile_photo_url or "").strip()
        or None
    )
    if not source:
        return existing_photo_url

    from core.images import copy_remote_image_to_s3

    key = await copy_remote_image_to_s3(source, prefix="profiles")
    if key:
        return key
    # Failed copy: never persist the provider HTTP/HTTPS URL. Keep a
    # pre-existing DB value (legacy URL or None); otherwise store None.
    if existing_photo_url and str(existing_photo_url).strip():
        return str(existing_photo_url).strip()
    return None

async def complete_firebase_registration(firebase_user: dict, db: AsyncSession) -> User:
    firebase_uid = firebase_user["uid"]
    stmt = select(User).options(selectinload(User.roles)).where(User.firebase_uid == firebase_uid)
    user = (await db.execute(stmt)).scalar_one_or_none()
    now = _now()

    if user:
        if user.status == UserStatus.deleting or user.deleted_at:
            from apps.user_deletion.services.account_recovery_service import (
                restore_deleting_account_if_eligible,
                run_recovery_side_effects,
            )

            restored = await restore_deleting_account_if_eligible(user, db)
            if not restored:
                raise ApiError(inactive_account_message(UserStatus.deleting))
            await db.commit()
            await run_recovery_side_effects(user, db)
        if user.status in (UserStatus.suspended, UserStatus.banned):
            raise ApiError(inactive_account_message(user.status))

        if not firebase_email_matches_user(firebase_user, user):
            raise ApiError(SOCIAL_EMAIL_MISMATCH_MESSAGE)

        if is_soft_deleted_user(user):
            reactivate_soft_deleted_user(user, now=now, firebase_uid=firebase_uid)
            db.add(user)
            await log_security_event(db, user.id, SecurityEventType.LOGIN_SUCCESS)
            await db.commit()
            await db.refresh(user)
            return (
                await db.execute(
                    select(User).options(selectinload(User.roles)).where(User.id == user.id)
                )
            ).scalar_one()

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
            if existing_user.status in (UserStatus.suspended, UserStatus.banned):
                raise ApiError(inactive_account_message(existing_user.status))
            reactivate_soft_deleted_user(
                existing_user,
                now=now,
                firebase_uid=firebase_uid,
            )
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
        stored_photo = await _store_social_profile_photo(
            firebase_user=firebase_user,
            payload=SocialAuthRequest(
                loginType="google",
                firebaseId=firebase_uid,
            ),
            existing_photo_url=None,
        )
        profile = Profile(
            user_id=user.id,
            first_name=display_parts[0] if display_parts else "",
            last_name=display_parts[1] if len(display_parts) > 1 else "",
            profile_photo_url=stored_photo,
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
    fcm_token: str | None = None,
) -> None:
    try:
        from apps.notifications.services.topic_service import TopicService

        profile = await _fetch_user_profile(db, user)
        if profile is not None:
            await TopicService.refresh_user_topic_subscriptions(
                db,
                user.id,
                profile,
                fcm_token=fcm_token,
            )
    except Exception:
        logger.exception(
            "Firebase topic sync failed during social auth (%s) user_id=%s",
            context,
            user.id,
        )


async def _build_device_auth_session(
    db: AsyncSession,
    user: User,
    device_id: str | None,
    *,
    platform: str | None = None,
    fcm_token: str | None = None,
) -> tuple[dict, str]:
    from sqlalchemy.orm import selectinload

    normalized_device_id = (device_id or "").strip() or None
    installation, is_new_device, needs_otp = await evaluate_device_otp_requirement(
        db,
        user,
        normalized_device_id,
    )

    if needs_otp:
        email_sent = await begin_otp_challenge(
            db,
            user,
            normalized_device_id,
            installation=installation,
            is_new_device=is_new_device,
            platform=platform,
            fcm_token=fcm_token,
        )
        await _refresh_user_topic_subscriptions_best_effort(
            db,
            user,
            context="otp flow",
            fcm_token=fcm_token,
        )
        stmt_user = select(User).options(selectinload(User.roles)).where(User.id == user.id)
        user = (await db.execute(stmt_user)).scalar_one()
        profile = await _fetch_user_profile(db, user)
        message = (
            "Verification email sent. Please verify your OTP."
            if email_sent
            else "Please verify your OTP."
        )
        return (
            attach_otp_flags(
                await _issue_auth_session(user, db, profile=profile),
                email_sent=email_sent,
                needs_otp=True,
                is_device_verified=False,
            ),
            message,
        )

    user.status = UserStatus.active
    user.updated_at = _now()
    db.add(user)

    if normalized_device_id:
        await upsert_user_installation(
            db,
            user.id,
            normalized_device_id,
            platform=platform,
            fcm_token=fcm_token,
            now=_now(),
            installation=installation,
        )

    await db.commit()
    await _refresh_user_topic_subscriptions_best_effort(
        db,
        user,
        context="no-otp flow",
        fcm_token=fcm_token,
    )
    signed_in_user_id = user.id
    stmt_user = select(User).options(selectinload(User.roles)).where(User.id == signed_in_user_id)
    refreshed = (await db.execute(stmt_user)).scalar_one_or_none()
    if refreshed is not None:
        user = refreshed

    from common.enums import UserActivityLogType
    from apps.analytics.services import add_user_activity_log_best_effort

    await add_user_activity_log_best_effort(
        db,
        signed_in_user_id,
        UserActivityLogType.SIGN_IN,
    )

    profile = await _fetch_user_profile(db, user)
    device_is_verified = bool(
        installation and getattr(installation, "is_device_verified", False)
    )
    return (
        attach_otp_flags(
            await _issue_auth_session(user, db, profile=profile),
            email_sent=False,
            needs_otp=False,
            is_device_verified=device_is_verified,
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

    ensure_public_signup_role(payload.user)

    # 3. Verify Firebase token properly
    try:
        firebase_user = verify_firebase_token(payload.firebaseId, check_revoked=True)
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
        expected_token_provider = "google.com"  # nosec B105 -- OAuth provider identifier, not a password
        provider_name = "google"
    elif requested_provider in ("apple", "apple.com"):
        expected_token_provider = "apple.com"  # nosec B105 -- OAuth provider identifier, not a password
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

    device_id = (payload.device_id or "").strip() or None
    if device_id:
        from apps.accounts.services.device_limit_service import validate_device_account_limit
        await validate_device_account_limit(db, device_id, user_id=user.id if user else None)

    now = _now()

    if not user:
        stmt_email = select(User).options(selectinload(User.roles)).where(User.email == email)
        existing_by_email = (await db.execute(stmt_email)).scalar_one_or_none()
        if existing_by_email:
            if existing_by_email.registration_type == RegistrationType(provider_name):
                if existing_by_email.status in (UserStatus.suspended, UserStatus.banned):
                    raise HTTPException(
                        status_code=status.HTTP_200_OK,
                        detail=inactive_account_message(existing_by_email.status)
                    )
                reactivate_soft_deleted_user(
                    existing_by_email,
                    now=now,
                    firebase_uid=uid,
                )
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
            from apps.user_deletion.services.account_recovery_service import (
                restore_deleting_account_if_eligible,
                run_recovery_side_effects,
            )

            restored = await restore_deleting_account_if_eligible(user, db)
            if not restored:
                raise HTTPException(
                    status_code=status.HTTP_200_OK,
                    detail=inactive_account_message(UserStatus.deleting)
                )
            await db.flush()
            await run_recovery_side_effects(user, db)
        if user.status in (UserStatus.suspended, UserStatus.banned):
            raise HTTPException(
                status_code=status.HTTP_200_OK,
                detail=inactive_account_message(user.status)
            )

        if is_soft_deleted_user(user):
            reactivate_soft_deleted_user(user, now=now, firebase_uid=uid)

        if not firebase_email_matches_user(firebase_user, user):
            raise ApiError(SOCIAL_EMAIL_MISMATCH_MESSAGE)

        user.last_login_at = now
        user.updated_at = now
        db.add(user)

        stmt_profile = select(Profile).where(Profile.user_id == user.id)
        profile = (await db.execute(stmt_profile)).scalar_one_or_none()
        if profile:
            stored_photo = await _store_social_profile_photo(
                firebase_user=firebase_user,
                payload=payload,
                existing_photo_url=profile.profile_photo_url,
            )
            if stored_photo and stored_photo != profile.profile_photo_url:
                profile.profile_photo_url = stored_photo
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

    from common.email_validation import validate_disposable_email
    from core.auth.config import settings as auth_settings

    validate_disposable_email(
        email,
        is_enabled=auth_settings.is_disposable_email_enabled,
    )

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

    await assign_user_role(db, user, "user")

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

    stored_photo = await _store_social_profile_photo(
        firebase_user=firebase_user,
        payload=payload,
        existing_photo_url=None,
    )
    profile = Profile(
        user_id=user.id,
        first_name=first_name,
        last_name=last_name,
        profile_photo_url=stored_photo,
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
    firebase_uid = firebase_user.get("uid")
    try:
        return await _signup_impl(payload, firebase_user, db)
    except ApiError as exc:
        await db.rollback()
        if exc.cleanup_firebase:
            delete_firebase_user_safely(firebase_uid)
        raise
    except Exception:
        await db.rollback()
        delete_firebase_user_safely(firebase_uid)
        logger.exception("Signup failed for firebase_uid=%s", firebase_uid)
        return ApiResponse(
            status=False,
            message=SIGNUP_GENERIC_FAILURE_MESSAGE,
            data=None,
        )


async def _signup_impl(
    payload: EmailSignupRequest,
    firebase_user: dict,
    db: AsyncSession,
) -> ApiResponse:
    if firebase_user.get("uid") is None or (firebase_user.get("email") or "").lower() != payload.email.lower():
        raise ApiError("Invalid Firebase credentials")
    ensure_public_signup_role(payload.role)
    email = payload.email.lower()

    from common.email_validation import validate_disposable_email

    validate_disposable_email(
        email,
        is_enabled=auth_settings.is_disposable_email_enabled,
    )

    device_id = (payload.device_id or "").strip() or None
    if device_id:
        from apps.accounts.services.device_limit_service import validate_device_account_limit
        await validate_device_account_limit(db, device_id)

    stmt = select(User).where(User.firebase_uid == firebase_user["uid"])
    exisiting_user = (await db.execute(stmt)).scalar_one_or_none()

    if exisiting_user:
        raise ApiError(PUBLIC_AUTH_ACCOUNT_EXISTS_MESSAGE, cleanup_firebase=False)

    now = _now()
    # 1. Check duplicate email
    stmt = select(User).options(selectinload(User.roles)).where(User.email == email)
    existing_user_email = (await db.execute(stmt)).scalar_one_or_none()
    if existing_user_email:
        if _existing_user_has_staff_role(existing_user_email) or await user_has_staff_role(
            db, existing_user_email.id
        ):
            raise ApiError(STAFF_PUBLIC_AUTH_NOT_ALLOWED_MESSAGE)
        if is_soft_deleted_user(existing_user_email):
            if not is_purge_window_expired(existing_user_email, now=now):
                raise ApiError(PUBLIC_AUTH_ACCOUNT_EXISTS_MESSAGE, cleanup_firebase=False)
            await remove_expired_deleting_user_for_resignup(db, existing_user_email.id)
        elif existing_user_email.registration_type == RegistrationType.email:
            if existing_user_email.status in (UserStatus.suspended, UserStatus.banned):
                raise ApiError(inactive_account_message(existing_user_email.status))
            raise ApiError(PUBLIC_AUTH_ACCOUNT_EXISTS_MESSAGE, cleanup_firebase=False)
        else:
            reg_type_str = (
                existing_user_email.registration_type.value
                if hasattr(existing_user_email.registration_type, "value")
                else str(existing_user_email.registration_type)
            )
            raise ApiError(
                f"Account already exists. Please login using your registered method: {reg_type_str}"
            )

    # 2. Create User record from verified Firebase identity
    reg_type = RegistrationType.email
    password_hash = _hash_password(payload.password)
    if not password_hash:
        raise ApiError("Password is required")

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

    await assign_user_role(db, user, "user")

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
    device_id = (payload.device_id or "").strip() or None
    if device_id:
        await ensure_unverified_installation(
            db,
            user.id,
            device_id,
            platform=payload.platform,
            fcm_token=payload.fcm_token,
            now=now,
        )
    await save_current_consent(db, user.id, source=CONSENT_SOURCE_SIGNUP)
    await db.commit()

    email_sent = await begin_otp_challenge(
        db,
        user,
        device_id,
        is_new_device=True,
        platform=payload.platform,
        fcm_token=payload.fcm_token,
    )
    await db.refresh(user)
    await db.refresh(profile)
    stmt_user = select(User).options(selectinload(User.roles)).where(User.id == user.id)
    user = (await db.execute(stmt_user)).scalar_one()

    data = attach_otp_flags(
        await _issue_auth_session(user, db , profile=profile),
        email_sent=email_sent,
        needs_otp=True,
        is_device_verified=False,
    )
    return ApiResponse(status=True, message="Signup successful", data=data)
