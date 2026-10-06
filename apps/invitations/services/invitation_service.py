from __future__ import annotations

import re
import secrets
import string
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from apps.invitations.config import INVITATION_CODE_MAX_LENGTH, settings
from apps.invitations.db_models import Invitation
from apps.invitations.repositories import (
    count_invitations,
    count_invitations_associated_by_user_between,
    count_invitations_created_by_user_between,
    count_invitations_status_summary,
    create_invitation as persist_invitation,
    get_invitation_by_code,
    get_invitation_by_code_for_update,
    invitation_code_exists,
    list_invitations_with_inviter,
)
from apps.invitations.schemas import (
    AdminInvitationItem,
    AdminInvitationListResponse,
    InvitationAssociateData,
    InvitationAssociateResponse,
    InvitationCreateData,
    InvitationCreateResponse,
    InvitationRedeemData,
    InvitationRedeemResponse,
    InvitationValidateData,
    InvitationValidateResponse,
    SoftDeleteInvitationResponse,
)
from apps.profiles.db_models import Profile
from common.enums import AdminInvitationListStatus, InvitationStatus
from common.pagination import build_paginated_response
from common.responses import error_response, success_response
from common.time import calendar_day_bounds_utc, utc_now

INVALID_OR_EXPIRED_MESSAGE = "Invalid or expired invitation code"
DAILY_LIMIT_MESSAGE = "Daily invitation limit reached"
NOT_FOUND_MESSAGE = "Invitation code not found"
CODE_ALREADY_EXISTS_MESSAGE = "This code is used, please generate a new code"
CODE_LETTER_PREFIX_LEN = 3
MAX_CODE_GENERATION_ATTEMPTS = 20
_CODE_PATTERN = re.compile(r"^[A-Z]{3}[0-9]+$")
# int32 namespace for pg_advisory_xact_lock(ns, user_key) on /associate.
ASSOCIATION_DAILY_LIMIT_LOCK_NAMESPACE = 87421001
CREATION_DAILY_LIMIT_LOCK_NAMESPACE = 87421002
_PG_INT32_MAX = (2**31) - 1


def utc_day_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Calendar-day bounds in ``ANALYTICS_TIMEZONE``, returned as UTC."""
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


def is_stored_code_length_valid(code: str) -> bool:
    return bool(code) and len(code) <= INVITATION_CODE_MAX_LENGTH


def invitation_owner_user_id(invitation: Invitation) -> UUID | None:
    """User who issued the code: generator (inviter) or associator (redeemed_by)."""
    return invitation.inviter_user_id or invitation.redeemed_by_user_id


async def connect_users_from_invitation(
    db: AsyncSession,
    *,
    owner_user_id: UUID,
    redeemed_by_user_id: UUID,
) -> None:
    """Create the accepted lynkup between the invitation owner and the onboarded user."""
    from apps.connections.db_models import Connection, ConnectionRequest
    from apps.connections.services.connection_service import build_connection_pair
    from sqlmodel import select

    conn_req = ConnectionRequest(
        sender_user_id=owner_user_id,
        receiver_user_id=redeemed_by_user_id,
        status="accepted",
    )
    db.add(conn_req)

    low_id, high_id = build_connection_pair(owner_user_id, redeemed_by_user_id)
    conn_check = select(Connection).where(
        Connection.user_low_id == low_id,
        Connection.user_high_id == high_id,
    )
    conn_res = await db.execute(conn_check)
    existing_conn = conn_res.scalars().first()

    if existing_conn:
        if not existing_conn.is_active:
            existing_conn.is_active = True
            db.add(existing_conn)
    else:
        new_conn = Connection(user_low_id=low_id, user_high_id=high_id, is_active=True)
        db.add(new_conn)
    await db.flush()


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
    inviter_user_id: UUID | None,
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


def _invitation_status_value(invitation: Invitation) -> str:
    if hasattr(invitation.status, "value"):
        return invitation.status.value
    return str(invitation.status)


def effective_invitation_status(invitation: Invitation, *, now: datetime | None = None) -> str:
    """Status exposed by validate: stored status, or EXPIRED once expires_at has passed."""
    stored = _invitation_status_value(invitation)
    if stored == InvitationStatus.deactivated.value:
        return InvitationStatus.deactivated.value
    if stored == InvitationStatus.redeemed.value:
        return InvitationStatus.redeemed.value
    current = now or utc_now()
    if stored == InvitationStatus.expired.value or invitation.expires_at <= current:
        return InvitationStatus.expired.value
    return InvitationStatus.active.value


def admin_list_invitation_status(
    invitation: Invitation,
    *,
    now: datetime | None = None,
) -> str:
    """Effective admin-list status aligned with GET /admin/invitations filters."""
    if invitation.deleted_at is not None:
        return AdminInvitationListStatus.deleted.value
    stored = _invitation_status_value(invitation)
    if stored == InvitationStatus.redeemed.value:
        return AdminInvitationListStatus.redeemed.value
    current = now or utc_now()
    if stored == InvitationStatus.expired.value or (
        stored == InvitationStatus.active.value and invitation.expires_at <= current
    ):
        return AdminInvitationListStatus.expired.value
    return AdminInvitationListStatus.active.value


async def _lock_user_association_daily_limit(db: AsyncSession, user_id: UUID) -> None:
    """Hold a transaction-scoped advisory lock so concurrent /associate calls serialize per user."""
    await _lock_user_daily_limit(db, ASSOCIATION_DAILY_LIMIT_LOCK_NAMESPACE, user_id)


async def lock_invitation_creation_daily_limit(db: AsyncSession, user_id: UUID) -> None:
    """Hold a transaction-scoped advisory lock so concurrent invitation creates serialize per user."""
    await _lock_user_daily_limit(db, CREATION_DAILY_LIMIT_LOCK_NAMESPACE, user_id)


async def _lock_user_daily_limit(db: AsyncSession, namespace: int, user_id: UUID) -> None:
    key = int(user_id.int % _PG_INT32_MAX)
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:ns, :key)"),
        {"ns": namespace, "key": key},
    )


async def _allocate_unique_code(db: AsyncSession) -> str:
    for _ in range(MAX_CODE_GENERATION_ATTEMPTS):
        code = generate_invitation_code()
        if not await invitation_code_exists(db, code):
            return code
    raise RuntimeError("Failed to generate a unique invitation code")


async def create_invitation(
    db: AsyncSession,
    inviter_user_id: UUID,
) -> InvitationCreateResponse:
    now = utc_now()
    day_start, day_end = utc_day_bounds(now)
    await lock_invitation_creation_daily_limit(db, inviter_user_id)
    created_today = await count_invitations_created_by_user_between(
        db,
        inviter_user_id,
        start_at=day_start,
        end_at=day_end,
    )
    if created_today >= settings.daily_limit:
        await db.rollback()
        return error_response(
            DAILY_LIMIT_MESSAGE,
            response_cls=InvitationCreateResponse,
        )

    try:
        code = await _allocate_unique_code(db)
    except Exception:
        await db.rollback()
        return error_response(
            "Failed to generate a unique invitation code",
            response_cls=InvitationCreateResponse,
        )
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
        return error_response(
            "Failed to create invitation code",
            response_cls=InvitationCreateResponse,
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


async def associate_invitation(
    db: AsyncSession,
    user_id: UUID,
    code: str,
) -> InvitationAssociateResponse:
    normalized = normalize_invitation_code(code)
    if normalized is None or not is_stored_code_length_valid(normalized):
        return error_response(
            INVALID_OR_EXPIRED_MESSAGE,
            response_cls=InvitationAssociateResponse,
        )

    if await invitation_code_exists(db, normalized):
        return error_response(
            CODE_ALREADY_EXISTS_MESSAGE,
            response_cls=InvitationAssociateResponse,
        )

    now = utc_now()
    day_start, day_end = utc_day_bounds(now)
    await _lock_user_association_daily_limit(db, user_id)
    associated_today = await count_invitations_associated_by_user_between(
        db,
        user_id,
        start_at=day_start,
        end_at=day_end,
    )
    if associated_today >= settings.daily_limit:
        await db.rollback()
        return error_response(
            DAILY_LIMIT_MESSAGE,
            response_cls=InvitationAssociateResponse,
        )

    expires_at = now + timedelta(days=settings.block_days)
    try:
        invitation = await persist_invitation(
            db,
            inviter_user_id=None,
            code=normalized,
            expires_at=expires_at,
            status=InvitationStatus.active,
            redeemed_by_user_id=user_id,
        )
        await db.commit()
        await db.refresh(invitation)
    except IntegrityError:
        await db.rollback()
        return error_response(
            CODE_ALREADY_EXISTS_MESSAGE,
            response_cls=InvitationAssociateResponse,
        )
    except Exception:
        await db.rollback()
        return error_response(
            "Failed to create invitation code",
            response_cls=InvitationAssociateResponse,
        )

    return success_response(
        "Invitation code associated successfully",
        InvitationAssociateData(
            id=invitation.id,
            code=invitation.code,
            status=_invitation_status_value(invitation),
            redeemed_by_user_id=invitation.redeemed_by_user_id or user_id,
        ),
        response_cls=InvitationAssociateResponse,
    )


async def validate_invitation(
    db: AsyncSession,
    code: str,
) -> InvitationValidateResponse:
    normalized = normalize_invitation_code(code)
    if normalized is None or not is_stored_code_length_valid(normalized):
        return error_response(
            INVALID_OR_EXPIRED_MESSAGE,
            response_cls=InvitationValidateResponse,
        )

    invitation = await get_invitation_by_code(db, normalized)
    if invitation is None:
        return error_response(
            INVALID_OR_EXPIRED_MESSAGE,
            response_cls=InvitationValidateResponse,
        )

    return success_response(
        "success",
        InvitationValidateData(
            code=invitation.code,
            status=effective_invitation_status(invitation),
        ),
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
    Mark an invitation as redeemed when used during onboarding and connect the
    onboarded user to the code owner (generator via inviter_user_id, or
    associator via redeemed_by_user_id).

    Raises HTTPException 400 when the code is invalid/expired/already used.
    Uses row-level locking to prevent concurrent redemptions.
    """
    normalized = normalize_invitation_code(code)
    if normalized is None or not is_stored_code_length_valid(normalized):
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

    owner_user_id = invitation_owner_user_id(invitation)
    if owner_user_id is not None and owner_user_id == redeemed_by_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot redeem your own invitation code",
        )

    invitation.redeemed_by_user_id = redeemed_by_user_id
    invitation.redeemed_at = now
    invitation.redemption_count = (invitation.redemption_count or 0) + 1
    invitation.is_converted = True
    invitation.status = InvitationStatus.redeemed
    invitation.updated_at = now
    db.add(invitation)
    await db.flush()

    try:
        if owner_user_id is not None:
            await connect_users_from_invitation(
                db,
                owner_user_id=owner_user_id,
                redeemed_by_user_id=redeemed_by_user_id,
            )

        if commit:
            await db.commit()
            await db.refresh(invitation)
    except Exception:
        if commit:
            await db.rollback()
        raise

    return invitation


async def redeem_invitation_response(
    db: AsyncSession,
    *,
    code: str,
    redeemed_by_user_id: UUID,
) -> InvitationRedeemResponse:
    """Redeem an invitation for the authenticated user and return ApiResponse."""
    try:
        invitation = await redeem_invitation(
            db,
            code=code,
            redeemed_by_user_id=redeemed_by_user_id,
            commit=True,
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return error_response(detail, response_cls=InvitationRedeemResponse)
    except Exception:
        await db.rollback()
        return error_response(
            "Failed to redeem invitation",
            response_cls=InvitationRedeemResponse,
        )

    return success_response(
        "Invitation redeemed successfully",
        InvitationRedeemData(
            code=invitation.code,
            inviter_user_id=invitation.inviter_user_id,
            redeemed_by_user_id=invitation.redeemed_by_user_id or redeemed_by_user_id,
            redemption_count=invitation.redemption_count,
            is_converted=invitation.is_converted,
        ),
        response_cls=InvitationRedeemResponse,
    )


async def get_all_invitations(
    db: AsyncSession,
    *,
    page: int | None = None,
    page_size: int | None = None,
    search: str | None = None,
    status: str | None = None,
) -> AdminInvitationListResponse:
    total_items = await count_invitations(db, search=search, status=status)
    summary = await count_invitations_status_summary(db, search=search)
    rows = await list_invitations_with_inviter(
        db, page=page, page_size=page_size, search=search, status=status
    )
    items = [
        AdminInvitationItem(
            code=invitation.code,
            **_inviter_fields(invitation.inviter_user_id, user, profile),
            status=admin_list_invitation_status(invitation),
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
    response_data = paginated.model_dump()
    response_data["summary"] = summary
    return success_response(
        "Invitation codes fetched successfully",
        response_data,
        response_cls=AdminInvitationListResponse,
    )


async def soft_delete_invitation(
    db: AsyncSession,
    *,
    code: str,
    admin_user_id: UUID,
    actor_role: str | None = None,
) -> SoftDeleteInvitationResponse:
    normalized = normalize_invitation_code(code)
    if normalized is None or not is_stored_code_length_valid(normalized):
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
    old_status = invitation.status.value if hasattr(invitation.status, "value") else str(invitation.status)
    old_is_active = bool(invitation.is_active)
    invitation.deleted_at = now
    invitation.status = InvitationStatus.deactivated
    invitation.is_active = False
    invitation.deactivated_by = admin_user_id
    invitation.updated_at = now
    db.add(invitation)

    from apps.administration.services.admin_activity_log_service import create_admin_activity_log

    await create_admin_activity_log(
        db,
        user_id=admin_user_id,
        role=actor_role,
        action="delete",
        module="invitation",
        record_id=invitation.id,
        description="deleted an invitation code",
        metadata={
            "old": {"status": old_status, "is_active": old_is_active, "deleted_at": None},
            "new": {
                "status": InvitationStatus.deactivated.value,
                "is_active": False,
                "deleted_at": now,
            },
        },
    )

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        return SoftDeleteInvitationResponse(
            status=False,
            message="Failed to delete invitation code",
            data={},
        )

    return SoftDeleteInvitationResponse(
        status=True,
        message="Invitation code deleted successfully",
        data={},
    )

