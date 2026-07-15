from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from apps.accounts.db_models import User
from apps.invitations.db_models import Invitation
from apps.profiles.db_models import Profile
from apps.profiles.db_models.university_db_model import University
from common.enums import InvitationStatus


async def get_invitation_by_code(
    db: AsyncSession,
    code: str,
    *,
    include_inviter: bool = False,
) -> Invitation | None:
    stmt = select(Invitation).where(Invitation.code == code)
    if include_inviter:
        stmt = stmt.options(selectinload(Invitation.inviter))
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_invitation_with_inviter_details(
    db: AsyncSession,
    code: str,
) -> tuple[Invitation, Profile | None, str | None] | None:
    """Return invitation with inviter profile and university name."""
    stmt = (
        select(Invitation, Profile, University.name)
        .outerjoin(Profile, Profile.user_id == Invitation.inviter_user_id)
        .outerjoin(University, University.id == Profile.university_id)
        .where(Invitation.code == code)
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
    stmt = (
        select(Invitation)
        .where(Invitation.code == code)
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


async def invitation_code_exists(db: AsyncSession, code: str) -> bool:
    stmt = select(Invitation.id).where(Invitation.code == code).limit(1)
    return (await db.execute(stmt)).scalar_one_or_none() is not None


async def count_invitations(db: AsyncSession) -> int:
    stmt = select(func.count()).select_from(Invitation)
    return int((await db.execute(stmt)).scalar_one())


async def list_invitations_with_inviter(
    db: AsyncSession,
    *,
    page: int | None = None,
    page_size: int | None = None,
) -> list[tuple[Invitation, User | None, Profile | None]]:
    """Return invitations joined with inviter user/profile, newest first.

    When page or page_size is None, returns all rows (no offset/limit).
    """
    stmt = (
        select(Invitation, User, Profile)
        .outerjoin(User, User.id == Invitation.inviter_user_id)
        .outerjoin(Profile, Profile.user_id == User.id)
        .order_by(Invitation.created_at.desc())
    )
    if page is not None and page_size is not None:
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(stmt)).all()
    return [(row.Invitation, row.User, row.Profile) for row in rows]


async def create_invitation(
    db: AsyncSession,
    *,
    inviter_user_id: UUID,
    code: str,
    expires_at: datetime,
    status: InvitationStatus = InvitationStatus.active,
) -> Invitation:
    invitation = Invitation(
        inviter_user_id=inviter_user_id,
        code=code,
        status=status,
        expires_at=expires_at,
        is_active=True,
        is_converted=False,
        redemption_count=0,
        redeemed_by_user_id=None,
        redeemed_at=None,
    )
    db.add(invitation)
    await db.flush()
    await db.refresh(invitation)
    return invitation
