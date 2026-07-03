from __future__ import annotations
from uuid import UUID
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from apps.connections.db_models import Connection
from apps.accounts.db_models import User, UserRole, Role
from apps.profiles.db_models.profile_db_model import Profile
from common.enums import ProfileVisibility, UserStatus
from core.images import generate_profile_image_url

def get_interest_overlap(interests_1: list[int] | None, interests_2: list[int] | None) -> int:
    if not interests_1 or not interests_2:
        return 0
    return len(set(interests_1).intersection(set(interests_2)))

async def get_user_connections(db: AsyncSession, user_id: UUID) -> set[UUID]:
    stmt = select(Connection).where(
        or_(Connection.user_low_id == user_id, Connection.user_high_id == user_id),
        Connection.is_active == True
    )
    result = await db.execute(stmt)
    connections = result.scalars().all()
    connected_ids = set()
    for conn in connections:
        if conn.user_low_id == user_id:
            connected_ids.add(conn.user_high_id)
        else:
            connected_ids.add(conn.user_low_id)
    return connected_ids

async def _build_connection_adjacency(db: AsyncSession) -> dict[UUID, set[UUID]]:
    stmt = select(Connection).where(Connection.is_active == True)
    result = await db.execute(stmt)
    adjacency: dict[UUID, set[UUID]] = {}
    for conn in result.scalars().all():
        adjacency.setdefault(conn.user_low_id, set()).add(conn.user_high_id)
        adjacency.setdefault(conn.user_high_id, set()).add(conn.user_low_id)
    return adjacency

def get_mutual_connections_count_from_adjacency(
    adjacency: dict[UUID, set[UUID]],
    user_id_1: UUID,
    user_id_2: UUID,
) -> int:
    return len(adjacency.get(user_id_1, set()).intersection(adjacency.get(user_id_2, set())))

async def get_mutual_connections_count(db: AsyncSession, user_id_1: UUID, user_id_2: UUID) -> int:
    conns_1 = await get_user_connections(db, user_id_1)
    conns_2 = await get_user_connections(db, user_id_2)
    return len(conns_1.intersection(conns_2))

def validate_visibility(profile: Profile | None) -> bool:
    vis = getattr(profile, "profile_visibility", ProfileVisibility.public)
    return vis == ProfileVisibility.public

def calculate_recommendation_score(
    p1: Profile, p2: Profile, mutual_connections_count: int
) -> float:
    score = 0.0

    if p1.major and p2.major and p1.major.strip().lower() == p2.major.strip().lower():
        score += 30.0

    if p1.minor and p2.minor and p1.minor.strip().lower() == p2.minor.strip().lower():
        score += 15.0

    if p1.university_id and p2.university_id and p1.university_id == p2.university_id:
        score += 20.0

    overlap = get_interest_overlap(p1.profile_interests_id, p2.profile_interests_id)
    if overlap > 0:
        score += 20.0

    if p1.edu_level and p2.edu_level and p1.edu_level == p2.edu_level:
        score += 10.0

    if mutual_connections_count > 0:
        score += 5.0

    return min(100.0, score)

async def dismiss_recommendation(db: AsyncSession, user_id: UUID, dismissed_id: UUID) -> None:
    pass

async def get_recommendations(db: AsyncSession, user_id: UUID) -> list[dict]:
    stmt = select(Profile).where(Profile.user_id == user_id)
    result = await db.execute(stmt)
    current_profile = result.scalars().first()

    if not current_profile:
        return []

    connection_adjacency = await _build_connection_adjacency(db)

    from apps.profiles.db_models.university_db_model import University
    profiles_stmt = (
        select(Profile, University.name)
        .join(User, User.id == Profile.user_id)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .outerjoin(University, Profile.university_id == University.id)
        .where(
            Profile.user_id != user_id,
            User.status == UserStatus.active,
            User.is_deleted == False,
            User.deleted_at.is_(None),
            Role.name.notin_(["moderator", "viewer", "superadmin"]),
        )
    )
    profiles_res = await db.execute(profiles_stmt)
    candidates_with_uni = profiles_res.all()

    scored_candidates = []

    for candidate, university_name in candidates_with_uni:
        mutuals = get_mutual_connections_count_from_adjacency(
            connection_adjacency, user_id, candidate.user_id
        )
        score = calculate_recommendation_score(current_profile, candidate, mutuals)

        scored_candidates.append({
            "user_id": candidate.user_id,
            "score": score,
            "first_name": candidate.first_name,
            "last_name": candidate.last_name,
            "university": university_name,
            "major": candidate.major,
            "minor": candidate.minor,
            "edu_level": candidate.edu_level,
            "profilePhoto_url": generate_profile_image_url(candidate.profile_photo_url) if candidate.profile_photo_url else None,
            "is_deleted": False,
        })

    scored_candidates.sort(key=lambda x: x["score"], reverse=True)

    target_user_ids = [c["user_id"] for c in scored_candidates]
    from apps.connections.services import get_relationship_flags
    flags_map = await get_relationship_flags(db, user_id, target_user_ids)

    from apps.connections.services.connection_service import apply_relationship_flags

    for c in scored_candidates:
        apply_relationship_flags(c, flags_map, c["user_id"])

    return scored_candidates
