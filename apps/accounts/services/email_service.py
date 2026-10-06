from __future__ import annotations

import logging

from firebase_admin import auth as firebase_auth
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from apps.accounts.db_models import RefreshToken, SecurityEventType, User
from common.exceptions import ApiError
from core.auth.config import settings as auth_settings
from core.auth.services import (
    create_firebase_custom_token,
    revoke_firebase_tokens,
    unlink_social_login_providers,
    update_firebase_email,
)

from ..schemas import ApiResponse, ChangeEmailRequest
from .common_service import (
    _fetch_user_profile,
    _generate_tokens,
    _now,
    _revoke_refresh_token_row,
    _store_refresh_token,
    log_security_event,
)

logger = logging.getLogger(__name__)


def _validate_self_service_email_change_permission(
    user: User,
    profile,
) -> ApiResponse | None:
    """Enforce post-graduation email change rules for self-service only."""
    from apps.profiles.services.response_service import is_graduation_completed

    if user.email_verified_at is None:
        return ApiResponse(
            status=False,
            message="Email must be verified before changing your email address.",
            data=None,
        )

    graduation_date = profile.graduation_date if profile is not None else None
    if not is_graduation_completed(graduation_date):
        return ApiResponse(
            status=False,
            message="Email can only be changed after graduation is completed.",
            data=None,
        )

    if user.has_changed_email_after_graduation:
        return ApiResponse(
            status=False,
            message="You have already changed your email after graduation.",
            data=None,
        )

    return None


async def apply_user_email_change(
    user: User,
    new_email: str,
    db: AsyncSession,
    *,
    revoke_sessions: bool = True,
) -> ApiResponse | None:
    """Validate and apply an email change in Firebase and on the in-memory user.

    Returns an error ``ApiResponse`` on failure, or ``None`` when the change was
    applied in memory and is ready to be committed by the caller.
    """
    normalized_email = new_email.lower().strip()
    current_email = (user.email or "").lower().strip()

    if normalized_email == current_email:
        return None

    from common.email_validation import validate_disposable_email

    try:
        validate_disposable_email(
            normalized_email,
            is_enabled=auth_settings.is_disposable_email_enabled,
        )
    except ApiError as exc:
        return ApiResponse(status=False, message=str(exc.message), data=None)

    if not user.firebase_uid:
        return ApiResponse(
            status=False,
            message="Email change is not available for this account",
            data=None,
        )

    duplicate_stmt = select(User).where(
        User.email == normalized_email,
        User.id != user.id,
    )
    duplicate_user = (await db.execute(duplicate_stmt)).scalar_one_or_none()
    if duplicate_user is not None:
        return ApiResponse(
            status=False,
            message="Email already registered",
            data=None,
        )

    firebase_uid = user.firebase_uid

    try:
        update_firebase_email(firebase_uid, normalized_email)
    except firebase_auth.EmailAlreadyExistsError:
        logger.warning(
            "Firebase email already exists for uid=%s new_email=%s",
            firebase_uid,
            normalized_email,
        )
        return ApiResponse(
            status=False,
            message="Email already registered",
            data=None,
        )
    except Exception as exc:
        logger.error(
            "Failed to update Firebase email for user %s: %s",
            user.id,
            exc,
        )
        return ApiResponse(
            status=False,
            message="Failed to update email in Firebase",
            data=None,
        )

    try:
        unlink_social_login_providers(firebase_uid)
    except Exception as exc:
        logger.error(
            "Failed to unlink social providers after email change for user %s: %s",
            user.id,
            exc,
        )
        await revert_user_email_change(user, current_email)
        return ApiResponse(
            status=False,
            message="Failed to update email in Firebase",
            data=None,
        )

    user.email = normalized_email
    user.email_verified_at = None
    user.email_otp = None
    user.email_otp_created_at = None
    user.updated_at = _now()
    db.add(user)

    if revoke_sessions:
        if firebase_uid:
            try:
                revoke_firebase_tokens(firebase_uid)
            except Exception as exc:
                logger.error(
                    "Failed to revoke Firebase tokens after email change for user %s: %s",
                    user.id,
                    exc,
                )
                await revert_user_email_change(user, current_email)
                user.email = current_email
                return ApiResponse(
                    status=False,
                    message=(
                        "Email could not be updated because other sessions could not be ended. "
                        "Try again."
                    ),
                    data=None,
                )

        try:
            rows = (
                await db.execute(
                    select(RefreshToken).where(
                        RefreshToken.user_id == user.id,
                        RefreshToken.revoked_at == None,  # noqa: E711
                    )
                )
            ).scalars().all()
            for row in rows:
                await _revoke_refresh_token_row(db, row)
        except Exception as exc:
            logger.warning(
                "Failed to revoke DB refresh tokens after email change for user %s: %s",
                user.id,
                exc,
            )

        try:
            from apps.chat.service import revoke_stream_user_tokens_best_effort

            await revoke_stream_user_tokens_best_effort(user)
        except Exception as exc:
            logger.warning(
                "Failed to revoke Stream tokens after email change for user %s: %s",
                user.id,
                exc,
            )

    await log_security_event(
        db,
        user.id,
        SecurityEventType.EMAIL_CHANGED,
        event_metadata={"old_email": current_email, "new_email": normalized_email},
    )

    return None


async def revert_user_email_change(user: User, previous_email: str) -> None:
    """Best-effort Firebase rollback when a PostgreSQL commit fails after email change."""
    if not user.firebase_uid:
        return
    try:
        update_firebase_email(user.firebase_uid, previous_email)
    except Exception:
        logger.exception(
            "Failed to revert Firebase email after PostgreSQL conflict for user %s",
            user.id,
        )


async def change_email(
    user: User,
    payload: ChangeEmailRequest,
    db: AsyncSession,
) -> ApiResponse:
    new_email = payload.newEmail.lower().strip()
    current_email = (user.email or "").lower().strip()

    if new_email == current_email:
        return ApiResponse(
            status=False,
            message="New email must be different from your current email",
            data=None,
        )

    profile = await _fetch_user_profile(db, user)
    permission_error = _validate_self_service_email_change_permission(user, profile)
    if permission_error is not None:
        return permission_error

    error_response = await apply_user_email_change(user, new_email, db)
    if error_response is not None:
        return error_response

    user.has_changed_email_after_graduation = True
    user.updated_at = _now()
    db.add(user)

    firebase_uid = user.firebase_uid

    try:
        access_token, refresh_token = _generate_tokens(user)
        await _store_refresh_token(db, user, refresh_token, device_id=None)

        firebase_custom_token: str | None = None
        if firebase_uid:
            try:
                firebase_custom_token = create_firebase_custom_token(firebase_uid)
            except Exception as exc:
                logger.warning(
                    "Failed to create Firebase custom token after email change for user %s: %s",
                    user.id,
                    exc,
                )

        await db.commit()
    except IntegrityError:
        await db.rollback()
        await revert_user_email_change(user, current_email)
        return ApiResponse(
            status=False,
            message="Email already registered",
            data=None,
        )

    await db.refresh(user)
    profile = await _fetch_user_profile(db, user)
    from apps.profiles.services import build_user_base_response

    user_data = await build_user_base_response(
        user,
        profile,
        db,
        viewer_user_id=user.id,
    )

    return ApiResponse(
        status=True,
        message="Email updated successfully",
        data={
            "user": user_data,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",  # nosec B105 -- token type constant, not a password
            "firebaseCustomToken": firebase_custom_token,
            "needsOtp": True,
        },
    )
