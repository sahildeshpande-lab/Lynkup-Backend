from __future__ import annotations

from uuid import UUID
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.connections.db_models import Block, Connection
from apps.accounts.db_models import User, UserRole, Role
from apps.profiles.db_models.profile_db_model import Profile
from common.enums import ProfileVisibility, UserStatus
from core.images import generate_profile_image_url


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def get_interest_overlap(interests_1: list[int] | None, interests_2: list[int] | None) -> int:
    if not interests_1 or not interests_2:
        return 0
    return len(set(interests_1).intersection(set(interests_2)))


async def get_user_connections(db: AsyncSession, user_id: UUID) -> set[UUID]:
    stmt = select(Connection).where(
        or_(Connection.user_low_id == user_id, Connection.user_high_id == user_id),
        Connection.is_active == True,
    )
    result = await db.execute(stmt)
    connections = result.scalars().all()
    connected_ids: set[UUID] = set()
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


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _build_match_reasons(p1: Profile, p2: Profile, mutual_connections_count: int) -> list[str]:
    """Return human-readable match reasons for a candidate."""
    reasons: list[str] = []

    if p1.major and p2.major and p1.major.strip().lower() == p2.major.strip().lower():
        reasons.append("Same major")

    if p1.minor and p2.minor and p1.minor.strip().lower() == p2.minor.strip().lower():
        reasons.append("Same minor")

    if p1.university_id and p2.university_id and p1.university_id == p2.university_id:
        reasons.append("Same university")

    overlap = get_interest_overlap(p1.profile_interests_id, p2.profile_interests_id)
    if overlap > 0:
        reasons.append(f"{overlap} shared interest{'s' if overlap > 1 else ''}")

    if p1.edu_level and p2.edu_level and p1.edu_level == p2.edu_level:
        reasons.append("Same education level")

    if mutual_connections_count > 0:
        reasons.append(
            f"{mutual_connections_count} mutual connection{'s' if mutual_connections_count > 1 else ''}"
        )

    return reasons


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


# ---------------------------------------------------------------------------
# Pre-fetch exclusion sets
# ---------------------------------------------------------------------------

async def _get_excluded_user_ids(db: AsyncSession, user_id: UUID) -> set[UUID]:
    """
    Return the set of user IDs that should never appear in recommendations:
    - Users who have blocked or been blocked by current_user
    """
    excluded: set[UUID] = set()

    # Blocked (either direction)
    block_stmt = select(Block).where(
        Block.is_active == True,
        or_(
            Block.blocker_user_id == user_id,
            Block.blocked_user_id == user_id,
        ),
    )
    for blk in (await db.execute(block_stmt)).scalars().all():
        other = blk.blocked_user_id if blk.blocker_user_id == user_id else blk.blocker_user_id
        excluded.add(other)

    return excluded


# ---------------------------------------------------------------------------
# Main recommendation function
# ---------------------------------------------------------------------------

async def dismiss_recommendation(db: AsyncSession, user_id: UUID, dismissed_id: UUID) -> None:
    pass


async def get_recommendations(db: AsyncSession, user_id: UUID) -> list[dict]:
    """
    Return a scored, de-duplicated list of connection recommendations.

    Scoring weights:
      - Same major       : +30 pts   (global suggestion trigger)
      - Same university  : +20 pts
      - Shared interests : +20 pts
      - Same minor       : +15 pts   (global suggestion trigger)
      - Same edu level   : +10 pts
      - Mutual connection:  +5 pts

    Candidates are excluded if they are already connected, have a pending
    request, or are blocked in either direction.

    Only candidates with score > 0 are returned, ensuring that *global
    suggestions* (major / minor matches) are always surfaced while
    completely unrelated users are omitted.
    """
    # 1. Load the current user's profile
    stmt = select(Profile).where(Profile.user_id == user_id)
    result = await db.execute(stmt)
    current_profile = result.scalars().first()

    if not current_profile:
        return []

    # 2. Build adjacency for mutual-connection counts (single query)
    connection_adjacency = await _build_connection_adjacency(db)

    # 3. Fetch IDs to exclude (connected, pending, blocked)
    excluded_ids = await _get_excluded_user_ids(db, user_id)

    # 4. Fetch all eligible candidate profiles
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

    # Exclude already-connected / pending / blocked users at the DB level
    if excluded_ids:
        profiles_stmt = profiles_stmt.where(Profile.user_id.notin_(list(excluded_ids)))

    profiles_res = await db.execute(profiles_stmt)
    candidates_with_uni = profiles_res.all()

    # 5. Score and collect match reasons
    scored_candidates = []

    for candidate, university_name in candidates_with_uni:
        mutuals = get_mutual_connections_count_from_adjacency(
            connection_adjacency, user_id, candidate.user_id
        )
        score = calculate_recommendation_score(current_profile, candidate, mutuals)

        # Global suggestions: major/minor matches always have score > 0 (≥15 or ≥30).
        # Skip candidates with zero score — they share nothing in common.
        if score <= 0:
            continue

        match_reasons = _build_match_reasons(current_profile, candidate, mutuals)

        scored_candidates.append(
            {
                "user_id": candidate.user_id,
                "score": score,
                "match_reason": ", ".join(match_reasons) if match_reasons else None,
                "first_name": candidate.first_name,
                "last_name": candidate.last_name,
                "university": university_name,
                "major": candidate.major,
                "minor": candidate.minor,
                "edu_level": candidate.edu_level,
                "profilePhoto_url": (
                    generate_profile_image_url(candidate.profile_photo_url)
                    if candidate.profile_photo_url
                    else None
                ),
                "is_deleted": False,
            }
        )

    # 6. Sort by descending score
    scored_candidates.sort(key=lambda x: x["score"], reverse=True)

    # 7. Enrich with relationship flags
    target_user_ids = [c["user_id"] for c in scored_candidates]
    from apps.connections.services import get_relationship_flags
    flags_map = await get_relationship_flags(db, user_id, target_user_ids)

    from apps.connections.services.connection_service import apply_relationship_flags
    for c in scored_candidates:
        apply_relationship_flags(c, flags_map, c["user_id"])

    return scored_candidates
