from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import Role, User, UserRole
from apps.connections.db_models import Connection
from apps.profiles.db_models.profile_db_model import Profile
from apps.profiles.db_models.university_db_model import University
from common.enums import UserStatus


_STAFF_ROLES = ("moderator", "viewer", "superadmin")


def _connection_pair(user_id_1: UUID, user_id_2: UUID) -> tuple[UUID, UUID]:
    return (user_id_1, user_id_2) if user_id_1 < user_id_2 else (user_id_2, user_id_1)


async def get_active_connection_between(
    db: AsyncSession,
    user_id_1: UUID,
    user_id_2: UUID,
) -> Connection | None:
    """Return the active accepted connection between two users, if one exists."""
    low_id, high_id = _connection_pair(user_id_1, user_id_2)
    result = await db.execute(
        select(Connection).where(
            Connection.user_low_id == low_id,
            Connection.user_high_id == high_id,
            Connection.is_active == True,
        )
    )
    return result.scalar_one_or_none()


async def delete_connection(db: AsyncSession, connection: Connection) -> None:
    """Delete an accepted connection row."""
    await db.delete(connection)


async def get_active_connection_user_ids(db: AsyncSession, user_id: UUID) -> set[UUID]:
    """Return user IDs that have an active connection with ``user_id``."""
    stmt = select(Connection).where(
        or_(Connection.user_low_id == user_id, Connection.user_high_id == user_id),
        Connection.is_active == True,  # noqa: E712
    )
    result = await db.execute(stmt)
    connected_ids: set[UUID] = set()
    for conn in result.scalars().all():
        if conn.user_low_id == user_id:
            connected_ids.add(conn.user_high_id)
        else:
            connected_ids.add(conn.user_low_id)
    return connected_ids


def build_candidate_mutuals_from_connections(
    user_id: UUID,
    friend_ids: set[UUID],
    hop_connections: Iterable[Connection],
) -> dict[UUID, set[UUID]]:
    """Map each second-hop user to the friend IDs they share with ``user_id``."""
    candidate_mutuals: dict[UUID, set[UUID]] = {}
    for conn in hop_connections:
        low_id, high_id = conn.user_low_id, conn.user_high_id
        if low_id in friend_ids and high_id != user_id:
            candidate_mutuals.setdefault(high_id, set()).add(low_id)
        if high_id in friend_ids and low_id != user_id:
            candidate_mutuals.setdefault(low_id, set()).add(high_id)
    return candidate_mutuals


async def fetch_second_hop_mutuals(
    db: AsyncSession,
    user_id: UUID,
) -> tuple[set[UUID], dict[UUID, set[UUID]]]:
    """Return the viewer's friends and candidate → mutual-friend IDs.

    Uses two set-based connection queries (viewer's connections, then
    connections involving those friends) rather than per-candidate lookups.
    """
    friend_ids = await get_active_connection_user_ids(db, user_id)
    if not friend_ids:
        return set(), {}

    stmt = select(Connection).where(
        Connection.is_active == True,  # noqa: E712
        or_(
            Connection.user_low_id.in_(list(friend_ids)),
            Connection.user_high_id.in_(list(friend_ids)),
        ),
    )
    hop_connections = (await db.execute(stmt)).scalars().all()
    candidate_mutuals = build_candidate_mutuals_from_connections(
        user_id, friend_ids, hop_connections
    )
    return friend_ids, candidate_mutuals


async def fetch_eligible_recommendation_profiles(
    db: AsyncSession,
    viewer_id: UUID,
    candidate_ids: list[UUID],
) -> list[tuple[Profile, UUID | None, str | None, str | None]]:
    """Load eligible candidate profiles with university details in one query."""
    if not candidate_ids:
        return []

    stmt = (
        select(Profile, University.id, University.name, University.website)
        .join(User, User.id == Profile.user_id)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .outerjoin(University, Profile.university_id == University.id)
        .where(
            Profile.user_id.in_(candidate_ids),
            Profile.user_id != viewer_id,
            User.status == UserStatus.active,
            User.is_deleted == False,  # noqa: E712
            User.deleted_at.is_(None),
            Role.name.notin_(list(_STAFF_ROLES)),
        )
    )
    return list((await db.execute(stmt)).all())


async def fetch_visible_profiles_by_user_ids(
    db: AsyncSession,
    user_ids: list[UUID],
) -> dict[UUID, Profile]:
    """Batch-load active, non-deleted profiles for the given user IDs."""
    if not user_ids:
        return {}

    stmt = (
        select(Profile)
        .join(User, User.id == Profile.user_id)
        .where(
            Profile.user_id.in_(user_ids),
            User.status == UserStatus.active,
            User.is_deleted == False,  # noqa: E712
            User.deleted_at.is_(None),
        )
    )
    profiles = (await db.execute(stmt)).scalars().all()
    return {profile.user_id: profile for profile in profiles}
