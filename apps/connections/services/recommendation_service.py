from __future__ import annotations

from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import Role, User, UserRole
from apps.connections.db_models import Block, Connection, ConnectionRequest
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


def build_recommendation_profile_payload(
    candidate: Profile,
    uni_id,
    university_name: str | None,
    university_website: str | None,
) -> dict:
    """Shared profile fields used by people-recommendation responses."""
    return {
        "user_id": candidate.user_id,
        "first_name": candidate.first_name,
        "last_name": candidate.last_name,
        "university": university_name,
        "university_details": {
            "id": str(uni_id) if uni_id else (str(candidate.university_id) if candidate.university_id else None),
            "university_name": university_name,
            "university_website": university_website,
        },
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


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _has_major_match(p1: Profile, p2: Profile) -> bool:
    p1_major_id = getattr(p1, "major_id", None)
    p2_major_id = getattr(p2, "major_id", None)
    if p1_major_id is not None and p2_major_id is not None and p1_major_id == p2_major_id:
        return True
    if p1.major and p2.major and str(p1.major).strip().lower() == str(p2.major).strip().lower():
        return True
    return False


def _has_minor_match(p1: Profile, p2: Profile) -> bool:
    p1_minor_id = getattr(p1, "minor_id", None)
    p2_minor_id = getattr(p2, "minor_id", None)
    if p1_minor_id is not None and p2_minor_id is not None and p1_minor_id == p2_minor_id:
        return True
    if p1.minor and p2.minor and str(p1.minor).strip().lower() == str(p2.minor).strip().lower():
        return True
    return False


def _build_match_reasons(p1: Profile, p2: Profile, mutual_connections_count: int) -> list[str]:
    """Return human-readable match reasons for a candidate."""
    reasons: list[str] = []

    if _has_major_match(p1, p2):
        reasons.append("Same major")

    if _has_minor_match(p1, p2):
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

    if _has_major_match(p1, p2):
        score += 30.0

    if _has_minor_match(p1, p2):
        score += 15.0

    if p1.university_id and p2.university_id and p1.university_id == p2.university_id:
        score += 20.0

    overlap = get_interest_overlap(p1.profile_interests_id, p2.profile_interests_id)
    if overlap > 0:
        score += 20.0

    if p1.edu_level and p2.edu_level and p1.edu_level == p2.edu_level:
        score += 10.0

    mutual_score = min(mutual_connections_count * 10.0, 20.0)
    score += mutual_score

    return min(100.0, score)


# ---------------------------------------------------------------------------
# Pre-fetch exclusion sets
# ---------------------------------------------------------------------------

async def _get_excluded_user_ids(db: AsyncSession, user_id: UUID) -> set[UUID]:
    """
    Return user IDs that should never appear in recommendations:
    - Already connected users
    - Users with a pending request in either direction
    - Users who have blocked or been blocked by current_user
    """
    excluded: set[UUID] = set()

    connected_ids = await get_user_connections(db, user_id)
    excluded.update(connected_ids)

    pending_stmt = select(ConnectionRequest).where(
        ConnectionRequest.status == "pending",
        or_(
            ConnectionRequest.sender_user_id == user_id,
            ConnectionRequest.receiver_user_id == user_id,
        ),
    )
    for req in (await db.execute(pending_stmt)).scalars().all():
        other = (
            req.receiver_user_id
            if req.sender_user_id == user_id
            else req.sender_user_id
        )
        excluded.add(other)

    # Blocked (either direction)
    block_stmt = select(Block).where(
        Block.is_active == True,  # noqa: E712
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


async def get_recommendations_categorized(
    db: AsyncSession, user_id: UUID
) -> dict[str, list[dict]]:
    """
    Return connection recommendations split into:
      - 'items': full scored list
      - 'based_on_major_minor': up to 8 users matching on major/minor
      - 'without_major_minor': up to 8 users recommended without major/minor match
    """
    # 1. Load the current user's profile
    stmt = select(Profile).where(Profile.user_id == user_id)
    result = await db.execute(stmt)
    current_profile = result.scalars().first()

    if not current_profile:
        return {
            "items": [],
            "based_on_major_minor": [],
            "without_major_minor": [],
        }

    # 2. Build adjacency for mutual-connection counts (single query)
    connection_adjacency = await _build_connection_adjacency(db)

    # 3. Fetch IDs to exclude (connected, pending, blocked)
    excluded_ids = await _get_excluded_user_ids(db, user_id)

    # 4. Fetch all eligible candidate profiles
    from apps.profiles.db_models.university_db_model import University

    profiles_stmt = (
        select(Profile, University.id, University.name, University.website)
        .join(User, User.id == Profile.user_id)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .outerjoin(University, Profile.university_id == University.id)
        .where(
            Profile.user_id != user_id,
            User.status == UserStatus.active,
            User.is_deleted == False,  # noqa: E712
            User.deleted_at.is_(None),
            Role.name.notin_(["moderator", "viewer", "superadmin"]),
        )
    )

    # Exclude already-connected / pending / blocked users at the DB level
    if excluded_ids:
        profiles_stmt = profiles_stmt.where(Profile.user_id.notin_(list(excluded_ids)))

    profiles_res = await db.execute(profiles_stmt)
    candidates_with_uni = profiles_res.all()

    # 5. Score and partition candidates
    all_scored_candidates: list[dict] = []
    major_minor_candidates: list[dict] = []
    university_candidates: list[dict] = []
    without_major_minor_candidates: list[dict] = []
    fallback_without_major_minor: list[dict] = []

    for candidate, uni_id, university_name, university_website in candidates_with_uni:
        mutuals = get_mutual_connections_count_from_adjacency(
            connection_adjacency, user_id, candidate.user_id
        )
        score = calculate_recommendation_score(current_profile, candidate, mutuals)
        match_reasons = _build_match_reasons(current_profile, candidate, mutuals)

        candidate_data = build_recommendation_profile_payload(
            candidate, uni_id, university_name, university_website
        )
        candidate_data["score"] = score
        candidate_data["match_reason"] = ", ".join(match_reasons) if match_reasons else None
        candidate_data["mutual_connections_count"] = mutuals

        has_mm = _has_major_match(current_profile, candidate) or _has_minor_match(current_profile, candidate)
        has_uni = bool(
            current_profile.university_id
            and candidate.university_id
            and current_profile.university_id == candidate.university_id
        )

        if score > 0:
            all_scored_candidates.append(candidate_data)
            if has_mm:
                major_minor_candidates.append(candidate_data)
            else:
                without_major_minor_candidates.append(candidate_data)
            if has_uni:
                university_candidates.append(candidate_data)
        else:
            if not has_mm:
                fallback_without_major_minor.append(candidate_data)

    # 6. Sort lists by descending score
    all_scored_candidates.sort(key=lambda x: x["score"], reverse=True)
    major_minor_candidates.sort(key=lambda x: x["score"], reverse=True)
    university_candidates.sort(key=lambda x: x["score"], reverse=True)
    without_major_minor_candidates.sort(key=lambda x: x["score"], reverse=True)

    if len(without_major_minor_candidates) < 8 and fallback_without_major_minor:
        without_major_minor_candidates.extend(fallback_without_major_minor[: 8 - len(without_major_minor_candidates)])

    # Build based_on_major_minor: 4 based on major/minor + 4 based on university
    mm_selected = major_minor_candidates[:4]
    selected_mm_ids = {c["user_id"] for c in mm_selected}

    uni_selected = [c for c in university_candidates if c["user_id"] not in selected_mm_ids][:4]
    top_major_minor = mm_selected + uni_selected
    selected_combined_ids = {c["user_id"] for c in top_major_minor}

    # If either group had fewer than 4, fill up to 8 from remaining candidates
    if len(top_major_minor) < 8:
        for c in major_minor_candidates:
            if c["user_id"] not in selected_combined_ids:
                top_major_minor.append(c)
                selected_combined_ids.add(c["user_id"])
                if len(top_major_minor) >= 8:
                    break
    if len(top_major_minor) < 8:
        for c in university_candidates:
            if c["user_id"] not in selected_combined_ids:
                top_major_minor.append(c)
                selected_combined_ids.add(c["user_id"])
                if len(top_major_minor) >= 8:
                    break

    top_without_major_minor = without_major_minor_candidates[:8]

    # 7. Enrich with relationship flags
    all_target_ids = list(
        {c["user_id"] for c in all_scored_candidates + top_major_minor + top_without_major_minor}
    )
    from apps.connections.services import get_relationship_flags
    flags_map = await get_relationship_flags(db, user_id, all_target_ids)

    from apps.connections.services.connection_service import apply_relationship_flags
    for c in all_scored_candidates:
        apply_relationship_flags(c, flags_map, c["user_id"])
    for c in top_major_minor:
        apply_relationship_flags(c, flags_map, c["user_id"])
    for c in top_without_major_minor:
        apply_relationship_flags(c, flags_map, c["user_id"])

    return {
        "items": all_scored_candidates,
        "based_on_major_minor": top_major_minor,
        "without_major_minor": top_without_major_minor,
    }


async def get_recommendations(db: AsyncSession, user_id: UUID) -> list[dict]:
    """Return a scored, de-duplicated list of connection recommendations."""
    result = await get_recommendations_categorized(db, user_id)
    return result["items"]
