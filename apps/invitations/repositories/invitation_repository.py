from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from apps.accounts.db_models import User
from apps.invitations.db_models import Invitation
from apps.profiles.db_models import Profile
from apps.profiles.db_models.university_db_model import University
from common.enums import AdminInvitationListStatus, InvitationStatus
from common.time import utc_now


def _code_match_clause(code: str):
    """Case-insensitive match so Branch short-link codes and legacy ABC1234 codes both resolve."""
    normalized = (code or "").strip()
    if not normalized:
        return None
    return func.lower(Invitation.code) == normalized.lower()


async def get_invitation_by_code(
    db: AsyncSession,
    code: str,
    *,
    include_inviter: bool = False,
) -> Invitation | None:
    clause = _code_match_clause(code)
    if clause is None:
        return None
    stmt = select(Invitation).where(clause)
    if include_inviter:
        stmt = stmt.options(selectinload(Invitation.inviter))
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_invitation_with_inviter_details(
    db: AsyncSession,
    code: str,
) -> tuple[Invitation, Profile | None, str | None] | None:
    """Return invitation with inviter profile and university name."""
    clause = _code_match_clause(code)
    if clause is None:
        return None
    stmt = (
        select(Invitation, Profile, University.name)
        .outerjoin(Profile, Profile.user_id == Invitation.inviter_user_id)
        .outerjoin(University, University.id == Profile.university_id)
        .where(clause)
    )
    row = (await db.execute(stmt)).one_or_none()
    if row is None:
        return None
    return row.Invitation, row.Profile, row[2]

async def get_invitation_by_code_for_update(
    db: AsyncSession,
    code: str,
) -> Invitation | None:
    """Lock invitation row to prevent concurrent redemption races."""
    clause = _code_match_clause(code)
    if clause is None:
        return None
    stmt = (
        select(Invitation)
        .where(clause)
        .with_for_update()
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_invitation_by_id(
    db: AsyncSession,
    invitation_id: UUID,
    *,
    for_update: bool = False,
) -> Invitation | None:
    stmt = select(Invitation).where(Invitation.id == invitation_id)
    if for_update:
        stmt = stmt.with_for_update()
    return (await db.execute(stmt)).scalar_one_or_none()


async def count_invitations_created_by_user_between(
    db: AsyncSession,
    inviter_user_id: UUID,
    *,
    start_at: datetime,
    end_at: datetime,
) -> int:
    """Count invitations created by a user in a time window (e.g. UTC day)."""
    stmt = (
        select(func.count())
        .select_from(Invitation)
        .where(
            Invitation.inviter_user_id == inviter_user_id,
            Invitation.created_at >= start_at,
            Invitation.created_at < end_at,
        )
    )
    return int((await db.execute(stmt)).scalar_one())


async def count_invitations_associated_by_user_between(
    db: AsyncSession,
    redeemed_by_user_id: UUID,
    *,
    start_at: datetime,
    end_at: datetime,
) -> int:
    """COUNT(*) of FE-associated rows for a user in [start_at, end_at).

    Association rows are distinguished from generated invitations by
    ``inviter_user_id IS NULL`` with ``redeemed_by_user_id`` set.
    """
    stmt = (
        select(func.count())
        .select_from(Invitation)
        .where(
            Invitation.redeemed_by_user_id == redeemed_by_user_id,
            Invitation.inviter_user_id.is_(None),
            Invitation.created_at >= start_at,
            Invitation.created_at < end_at,
        )
    )
    return int((await db.execute(stmt)).scalar_one())


async def invitation_code_exists(db: AsyncSession, code: str) -> bool:
    clause = _code_match_clause(code)
    if clause is None:
        return False
    stmt = select(Invitation.id).where(clause).limit(1)
    return (await db.execute(stmt)).scalar_one_or_none() is not None


def _invitation_search_clause(search: str | None):
    """Match invitation code or inviter user name (first, last, full name).
    
    1. Converts user search query to lowercase (e.g. 'Abc1234' -> 'abc1234').
    2. Converts DB column values to lowercase using func.lower().
    3. Matches the converted lowercase values on both sides.
    """
    term = (search or "").strip().lower()
    if not term:
        return None
    like_pattern = f"%{term}%"
    full_name = func.concat(
        func.coalesce(Profile.first_name, ""),
        " ",
        func.coalesce(Profile.last_name, ""),
    )
    return or_(
        func.lower(Invitation.code).like(like_pattern),
        func.lower(func.coalesce(Profile.first_name, "")).like(like_pattern),
        func.lower(func.coalesce(Profile.last_name, "")).like(like_pattern),
        func.lower(full_name).like(like_pattern),
    )


_invitation_code_search_clause = _invitation_search_clause


def _exclude_standalone_deactivated_clause():
    """Hide DEACTIVATED rows that were never soft-deleted from admin list views."""
    return or_(
        Invitation.status != InvitationStatus.deactivated,
        Invitation.deleted_at.is_not(None),
    )


def _invitation_status_filter_clause(
    status_filter: AdminInvitationListStatus | str | None,
    *,
    now: datetime | None = None,
):
    """Build WHERE clause for admin invitation status filters.

    Mutual exclusivity:
    - Active: stored ACTIVE, not past expires_at, not soft-deleted
    - Expired: stored EXPIRED, or ACTIVE past expires_at; not soft-deleted
    - Redeemed: stored REDEEMED
    - Deleted: soft-deleted (deleted_at set)
    """
    if status_filter is None:
        return None
    if isinstance(status_filter, str):
        try:
            status_filter = AdminInvitationListStatus(status_filter)
        except ValueError:
            return None

    current = now or utc_now()
    if status_filter == AdminInvitationListStatus.active:
        return and_(
            Invitation.status == InvitationStatus.active,
            Invitation.expires_at > current,
            Invitation.deleted_at.is_(None),
        )
    if status_filter == AdminInvitationListStatus.redeemed:
        return Invitation.status == InvitationStatus.redeemed
    if status_filter == AdminInvitationListStatus.deleted:
        return Invitation.deleted_at.is_not(None)
    if status_filter == AdminInvitationListStatus.expired:
        return and_(
            Invitation.deleted_at.is_(None),
            or_(
                Invitation.status == InvitationStatus.expired,
                and_(
                    Invitation.status == InvitationStatus.active,
                    Invitation.expires_at <= current,
                ),
            ),
        )
    return None


async def count_invitations_status_summary(
    db: AsyncSession,
    *,
    search: str | None = None,
) -> dict[str, int]:
    """Count invitations in each admin-list status bucket (respects search only)."""
    summary: dict[str, int] = {}
    total = 0
    for list_status in AdminInvitationListStatus:
        count = await count_invitations(db, search=search, status=list_status)
        summary[list_status.value.lower()] = count
        total += count
    summary["total"] = total
    return summary


async def count_invitations(
    db: AsyncSession,
    *,
    search: str | None = None,
    status: AdminInvitationListStatus | str | None = None,
) -> int:
    stmt = (
        select(func.count())
        .select_from(Invitation)
        .outerjoin(User, User.id == Invitation.inviter_user_id)
        .outerjoin(Profile, Profile.user_id == User.id)
    )
    search_clause = _invitation_search_clause(search)
    if search_clause is not None:
        stmt = stmt.where(search_clause)
    status_clause = _invitation_status_filter_clause(status)
    if status_clause is not None:
        stmt = stmt.where(status_clause)
    elif status is None:
        stmt = stmt.where(_exclude_standalone_deactivated_clause())
    return int((await db.execute(stmt)).scalar_one())


async def list_invitations_with_inviter(
    db: AsyncSession,
    *,
    page: int | None = None,
    page_size: int | None = None,
    search: str | None = None,
    status: AdminInvitationListStatus | str | None = None,
) -> list[tuple[Invitation, User | None, Profile | None]]:
    """Return invitations joined with inviter user/profile, newest first.

    When page or page_size is None, returns all rows (no offset/limit).
    """
    stmt = (
        select(Invitation, User, Profile)
        .outerjoin(User, User.id == Invitation.inviter_user_id)
        .outerjoin(Profile, Profile.user_id == User.id)
    )
    search_clause = _invitation_search_clause(search)
    if search_clause is not None:
        stmt = stmt.where(search_clause)
    status_clause = _invitation_status_filter_clause(status)
    if status_clause is not None:
        stmt = stmt.where(status_clause)
    elif status is None:
        stmt = stmt.where(_exclude_standalone_deactivated_clause())
    stmt = stmt.order_by(Invitation.created_at.desc())
    if page is not None and page_size is not None:
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(stmt)).all()
    return [(row.Invitation, row.User, row.Profile) for row in rows]


async def create_invitation(
    db: AsyncSession,
    *,
    inviter_user_id: UUID | None,
    code: str,
    expires_at: datetime,
    status: InvitationStatus = InvitationStatus.active,
    redeemed_by_user_id: UUID | None = None,
) -> Invitation:
    invitation = Invitation(
        inviter_user_id=inviter_user_id,
        code=code,
        status=status,
        expires_at=expires_at,
        is_active=True,
        is_converted=False,
        redemption_count=0,
        redeemed_by_user_id=redeemed_by_user_id,
        redeemed_at=None,
    )
    db.add(invitation)
    await db.flush()
    await db.refresh(invitation)
    return invitation
