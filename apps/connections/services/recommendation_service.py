from __future__ import annotations
from uuid import UUID
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from apps.connections.db_models import Block, Connection, ConnectionRequest
from apps.profiles.db_models.profile_db_model import Profile
from apps.profiles.db_models import Profile
from common.enums import ProfileVisibility

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

async def get_mutual_connections_count(db: AsyncSession, user_id_1: UUID, user_id_2: UUID) -> int:
    conns_1 = await get_user_connections(db, user_id_1)
    conns_2 = await get_user_connections(db, user_id_2)
    return len(conns_1.intersection(conns_2))

def validate_visibility(profile: Profile | None) -> bool:
    # MVP: only public profiles can be discovered in recommendations
    # Profile might not have a visibility attribute if it's missing from the model definition,
    # but based on rules: public = visible, private = never, connection_only = only if connected.
    # We will assume a `profile_visibility` field exists. If not, default to True.
    vis = getattr(profile, "profile_visibility", ProfileVisibility.public)
    return vis == ProfileVisibility.public

def calculate_recommendation_score(
    p1: Profile, p2: Profile, mutual_connections_count: int
) -> float:
    score = 0.0

    # major match = 30
    if p1.major and p2.major and p1.major.strip().lower() == p2.major.strip().lower():
        score += 30.0

    # minor match = 15
    if p1.minor and p2.minor and p1.minor.strip().lower() == p2.minor.strip().lower():
        score += 15.0

    # university match = 20
    if p1.university_id and p2.university_id and p1.university_id == p2.university_id:
        score += 20.0

    # academic interest overlap = 20
    # Assuming any overlap gives 20 points
    overlap = get_interest_overlap(p1.profile_interests_id, p2.profile_interests_id)
    if overlap > 0:
        score += 20.0

    # education proximity = 10
    if p1.edu_level and p2.edu_level and p1.edu_level == p2.edu_level:
        score += 10.0

    # mutual connections = 5
    if mutual_connections_count > 0:
        score += 5.0

    return min(100.0, score)

async def dismiss_recommendation(db: AsyncSession, user_id: UUID, dismissed_id: UUID) -> None:
    # MVP: Not tracking dismissed directly in a specific table based on requirements,
    # but a stub for future integration.
    pass

async def get_recommendations(db: AsyncSession, user_id: UUID) -> list[dict]:
    # 1. Fetch current user profile
    stmt = select(Profile).where(Profile.user_id == user_id)
    result = await db.execute(stmt)
    current_profile = result.scalars().first()

    if not current_profile:
        return []

    # 2. Fetch all user connections (to exclude and to calculate mutuals)
    current_connections = await get_user_connections(db, user_id)

    # 3. Fetch all blocked users (both ways)
    blocked_stmt = select(Block).where(
        Block.is_active == True,
        or_(Block.blocker_user_id == user_id, Block.blocked_user_id == user_id)
    )
    blocked_res = await db.execute(blocked_stmt)
    blocked_ids = set()
    for b in blocked_res.scalars().all():
        if b.blocker_user_id == user_id:
            blocked_ids.add(b.blocked_user_id)
        else:
            blocked_ids.add(b.blocker_user_id)

    # 4. Fetch all pending requests (both ways)
    # req_stmt = select(ConnectionRequest).where(
    #     ConnectionRequest.status == "pending",
    #     or_(ConnectionRequest.sender_user_id == user_id, ConnectionRequest.receiver_user_id == user_id)
    # )
    # req_res = await db.execute(req_stmt)
    # pending_ids = set()
    # for r in req_res.scalars().all():
    #     if r.sender_user_id == user_id:
    #         pending_ids.add(r.receiver_user_id)
    #     else:
    #         pending_ids.add(r.sender_user_id)

    # Combine excluded ids
    # excluded_ids = current_connections.union(blocked_ids).union(pending_ids)
    excluded_ids = current_connections.union(blocked_ids)
    excluded_ids.add(user_id) # self

    # 5. Fetch all potential profiles
    from apps.profiles.db_models.university_db_model import University
    profiles_stmt = select(Profile, University.name).outerjoin(
        University, Profile.university_id == University.id
    ).where(
        Profile.user_id.notin_(excluded_ids)
    )
    profiles_res = await db.execute(profiles_stmt)
    candidates_with_uni = profiles_res.all()

    scored_candidates = []

    for candidate, university_name in candidates_with_uni:
        if not validate_visibility(candidate):
            continue

        mutuals = await get_mutual_connections_count(db, user_id, candidate.user_id)
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
            "profile_photo_key": candidate.profile_photo_url,
        })

    scored_candidates.sort(key=lambda x: x["score"], reverse=True)
    return scored_candidates
