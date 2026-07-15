from __future__ import annotations

import re
import secrets
import string
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from apps.invitations.config import settings
from apps.invitations.db_models import Invitation
from apps.invitations.repositories import (
    count_invitations,
    count_invitations_created_by_user_between,
    create_invitation as persist_invitation,
    get_invitation_by_code_for_update,
    get_invitation_with_inviter_details,
    invitation_code_exists,
    list_invitations_with_inviter,
)
from apps.invitations.schemas import (
    AdminInvitationItem,
    AdminInvitationListResponse,
    InvitationCreateData,
    InvitationCreateResponse,
    InvitationValidateData,
    InvitationValidateResponse,
    SoftDeleteInvitationResponse,
)
from apps.profiles.db_models import Profile
from common.enums import InvitationStatus
from common.pagination import build_paginated_response
from common.responses import error_response, success_response
from common.time import calendar_day_bounds_utc, utc_now
from core.images import generate_profile_image_url

INVALID_OR_EXPIRED_MESSAGE = "Invalid or expired invitation code"
DAILY_LIMIT_MESSAGE = "Daily invitation limit reached"
NOT_FOUND_MESSAGE = "Invitation code not found"
CODE_LETTER_PREFIX_LEN = 3
MAX_CODE_GENERATION_ATTEMPTS = 20
_CODE_PATTERN = re.compile(r"^[A-Z]{3}[0-9]+$")


def utc_day_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Calendar-day bounds in ``INVITATION_TIMEZONE``, returned as UTC."""
    return calendar_day_bounds_utc(settings.timezone, now=now)


def generate_invitation_code() -> str:
    """
    Generate a code of ``INVITATION_CODE_LENGTH`` chars:

    first 3 characters are A-Z, remaining are digits (e.g. ABC1234).
    """
    length = max(settings.code_length, CODE_LETTER_PREFIX_LEN + 1)
    digit_len = length - CODE_LETTER_PREFIX_LEN
    prefix = "".join(secrets.choice(string.ascii_uppercase) for _ in range(CODE_LETTER_PREFIX_LEN))
    digits = f"{secrets.randbelow(10 ** digit_len):0{digit_len}d}"
    return f"{prefix}{digits}"


def normalize_invitation_code(code: str | None) -> str | None:
    if code is None:
        return None
    normalized = code.strip().upper()
    if not normalized:
        return None
    return normalized


def is_valid_code_format(code: str) -> bool:
    expected_len = settings.code_length
    return (
        len(code) == expected_len
        and _CODE_PATTERN.fullmatch(code) is not None
        and code[:CODE_LETTER_PREFIX_LEN].isalpha()
        and code[CODE_LETTER_PREFIX_LEN:].isdigit()
    )


def is_invitation_currently_valid(invitation: Invitation, *, now: datetime | None = None) -> bool:
    """Invitation is usable only when undeleted, active, unexpired, and unused."""
    current = now or utc_now()
    if invitation.deleted_at is not None:
        return False
    if not invitation.is_active:
        return False
    if invitation.status != InvitationStatus.active:
        return False
    if invitation.expires_at <= current:
        return False
    if invitation.redemption_count != 0:
        return False
    return True


def _inviter_fields(
    inviter_user_id: UUID,
    user: User | None,
    profile: Profile | None,
) -> dict:
    first_name = profile.first_name if profile else None
    last_name = profile.last_name if profile else None
    username = None
    if first_name or last_name:
        username = " ".join(part for part in (first_name, last_name) if part).strip() or None
    return {
        "user_id": user.id if user is not None else inviter_user_id,
        "first_name": first_name,
        "last_name": last_name,
        "username": username,
    }


def _inviter_validate_payload(
    inviter_user_id: UUID,
    profile: Profile | None,
    university_name: str | None,
) -> InvitationValidateData:
    photo_key = profile.profile_photo_url if profile else None
    return InvitationValidateData(
        user_id=inviter_user_id,
        first_name=profile.first_name if profile else None,
        last_name=profile.last_name if profile else None,
        profile_photo_url=generate_profile_image_url(photo_key) if photo_key else None,
        university=university_name,
        bio=profile.bio if profile else None,
    )


async def _allocate_unique_code(db: AsyncSession) -> str:
    for _ in range(MAX_CODE_GENERATION_ATTEMPTS):
        code = generate_invitation_code()
        if not await invitation_code_exists(db, code):
            return code
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Failed to generate a unique invitation code",
    )


async def create_invitation(
    db: AsyncSession,
    inviter_user_id: UUID,
) -> InvitationCreateResponse:
    now = utc_now()
    day_start, day_end = utc_day_bounds(now)
    created_today = await count_invitations_created_by_user_between(
        db,
        inviter_user_id,
        start_at=day_start,
        end_at=day_end,
    )
    if created_today >= settings.daily_limit:
        return error_response(
            DAILY_LIMIT_MESSAGE,
            response_cls=InvitationCreateResponse,
        )

    code = await _allocate_unique_code(db)
    expires_at = now + timedelta(days=settings.block_days)

    try:
        invitation = await persist_invitation(
            db,
            inviter_user_id=inviter_user_id,
            code=code,
            expires_at=expires_at,
            status=InvitationStatus.active,
        )
        await db.commit()
        await db.refresh(invitation)
    except IntegrityError:
        await db.rollback()
        return error_response(
            "Failed to create invitation code",
            response_cls=InvitationCreateResponse,
        )
    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create invitation code",
        )

    return success_response(
        "Invitation code created successfully",
        InvitationCreateData(
            id=invitation.id,
            code=invitation.code,
            expires_at=invitation.expires_at,
        ),
        response_cls=InvitationCreateResponse,
    )


async def validate_invitation(
    db: AsyncSession,
    code: str,
) -> InvitationValidateResponse:
    normalized = normalize_invitation_code(code)
    if normalized is None or not is_valid_code_format(normalized):
        return error_response(
            INVALID_OR_EXPIRED_MESSAGE,
            response_cls=InvitationValidateResponse,
        )

    details = await get_invitation_with_inviter_details(db, normalized)
    if details is None:
        return error_response(
            INVALID_OR_EXPIRED_MESSAGE,
            response_cls=InvitationValidateResponse,
        )

    invitation, profile, university_name = details
    if invitation.redemption_count > 0 or invitation.redeemed_by_user_id is not None:
        return error_response(
            "This code is used, please generate a new code",
            response_cls=InvitationValidateResponse,
        )

    if not is_invitation_currently_valid(invitation):
        return error_response(
            INVALID_OR_EXPIRED_MESSAGE,
            response_cls=InvitationValidateResponse,
        )

    return success_response(
        "Invitation code is valid",
        _inviter_validate_payload(invitation.inviter_user_id, profile, university_name),
        response_cls=InvitationValidateResponse,
    )


async def redeem_invitation(
    db: AsyncSession,
    *,
    code: str,
    redeemed_by_user_id: UUID,
    commit: bool = False,
) -> Invitation:
    """
    Mark an invitation as redeemed by a newly onboarded user.

    Raises HTTPException 400 when the code is invalid/expired/already used.
    Uses row-level locking to prevent concurrent redemptions.
    """
    normalized = normalize_invitation_code(code)
    if normalized is None or not is_valid_code_format(normalized):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=INVALID_OR_EXPIRED_MESSAGE,
        )

    invitation = await get_invitation_by_code_for_update(db, normalized)
    now = utc_now()
    if invitation is None or not is_invitation_currently_valid(invitation, now=now):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=INVALID_OR_EXPIRED_MESSAGE,
        )

    if invitation.inviter_user_id == redeemed_by_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot redeem your own invitation code",
        )

    invitation.redeemed_by_user_id = redeemed_by_user_id
    invitation.redeemed_at = now
    invitation.redemption_count = 1
    invitation.is_converted = True
    invitation.status = InvitationStatus.expired
    invitation.updated_at = now
    db.add(invitation)
    await db.flush()

    if commit:
        await db.commit()
        await db.refresh(invitation)

    return invitation


async def get_all_invitations(
    db: AsyncSession,
    *,
    page: int | None = None,
    page_size: int | None = None,
) -> AdminInvitationListResponse:
    total_items = await count_invitations(db)
    rows = await list_invitations_with_inviter(db, page=page, page_size=page_size)
    items = [
        AdminInvitationItem(
            code=invitation.code,
            **_inviter_fields(invitation.inviter_user_id, user, profile),
            status=(
                invitation.status.value
                if hasattr(invitation.status, "value")
                else str(invitation.status)
            ),
            expires_at=invitation.expires_at,
            deleted_at=invitation.deleted_at,
        )
        for invitation, user, profile in rows
    ]

    resolved_page = page if page is not None else 1
    resolved_page_size = page_size if page_size is not None else max(len(items), 1)
    paginated = build_paginated_response(
        items,
        resolved_page,
        resolved_page_size,
        total_items,
    )
    return success_response(
        "Invitation codes fetched successfully",
        paginated.model_dump(),
        response_cls=AdminInvitationListResponse,
    )


async def soft_delete_invitation(
    db: AsyncSession,
    *,
    code: str,
    admin_user_id: UUID,
) -> SoftDeleteInvitationResponse:
    normalized = normalize_invitation_code(code)
    if normalized is None or not is_valid_code_format(normalized):
        return SoftDeleteInvitationResponse(
            status=False,
            message=NOT_FOUND_MESSAGE,
            data={},
        )

    invitation = await get_invitation_by_code_for_update(db, normalized)
    if invitation is None:
        return SoftDeleteInvitationResponse(
            status=False,
            message=NOT_FOUND_MESSAGE,
            data={},
        )

    now = utc_now()
    invitation.deleted_at = now
    invitation.status = InvitationStatus.deactivated
    invitation.is_active = False
    invitation.deactivated_by = admin_user_id
    invitation.updated_at = now
    db.add(invitation)

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete invitation code",
        )

    return SoftDeleteInvitationResponse(
        status=True,
        message="Invitation code deleted successfully",
        data={},
    )

