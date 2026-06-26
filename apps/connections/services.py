from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Sequence
from uuid import UUID

from sqlalchemy import or_, and_, select, delete, update
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status

from apps.connections.db_models import Block,Connection,ConnectionRequest,ConnectionRecommendationSnapshot,Follow
from apps.profiles.db_models.profile_db_model import Profile
from sqlalchemy import update
from apps.profiles.db_models import Profile
from common.enums import ProfileVisibility
from apps.connections.schemas import ApiResponse


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def build_connection_pair(user_id_1: UUID, user_id_2: UUID) -> tuple[UUID, UUID]:
    if user_id_1 < user_id_2:
        return user_id_1, user_id_2
    return user_id_2, user_id_1


async def is_blocked(db: AsyncSession, user_id_1: UUID, user_id_2: UUID) -> bool:
    stmt = select(Block).where(
        Block.is_active == True,
        or_(
            and_(Block.blocker_user_id == user_id_1, Block.blocked_user_id == user_id_2),
            and_(Block.blocker_user_id == user_id_2, Block.blocked_user_id == user_id_1)
        )
    )
    result = await db.execute(stmt)
    return result.scalars().first() is not None


async def are_connected(db: AsyncSession, user_id_1: UUID, user_id_2: UUID) -> bool:
    low_id, high_id = build_connection_pair(user_id_1, user_id_2)
    stmt = select(Connection).where(
        Connection.user_low_id == low_id,
        Connection.user_high_id == high_id,
        Connection.is_active == True
    )
    result = await db.execute(stmt)
    return result.scalars().first() is not None


async def has_pending_request(db: AsyncSession, user_id_1: UUID, user_id_2: UUID) -> bool:
    stmt = select(ConnectionRequest).where(
        ConnectionRequest.status == "pending",
        or_(
            and_(ConnectionRequest.sender_user_id == user_id_1, ConnectionRequest.receiver_user_id == user_id_2),
            and_(ConnectionRequest.sender_user_id == user_id_2, ConnectionRequest.receiver_user_id == user_id_1)
        )
    )
    result = await db.execute(stmt)
    return result.scalars().first() is not None


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


async def send_connection_request(db: AsyncSession, sender_id: UUID, receiver_id: UUID) -> ApiResponse:
    if sender_id == receiver_id:
        return ApiResponse(status=True, message="Cannot send request to self.", data=[])
        
    if await is_blocked(db, sender_id, receiver_id):
        return ApiResponse(status=True, message="Cannot send request due to block.", data=[])
        
    if await are_connected(db, sender_id, receiver_id):
        return ApiResponse(status=True, message="Already connected.", data=[])
        
    if await has_pending_request(db, sender_id, receiver_id):
        return ApiResponse(status=True, message="Active request already exists.", data=[])
        
    request = ConnectionRequest(sender_user_id=sender_id, receiver_user_id=receiver_id, status="pending")
    db.add(request)
    await db.commit()
    await db.refresh(request)
    return ApiResponse(status=True, message="Connection request sent successfully.", data=request)


async def respond_connection_request(db: AsyncSession, user_id: UUID, other_user_id: UUID, response: str) -> ApiResponse:
    if response not in ["accepted", "declined"]:
        return ApiResponse(status=True, message="Invalid response.", data=[])
        
    stmt = select(ConnectionRequest).where(
        ConnectionRequest.status == "pending",
        or_(
            and_(ConnectionRequest.sender_user_id == other_user_id, ConnectionRequest.receiver_user_id == user_id),
            and_(ConnectionRequest.sender_user_id == user_id, ConnectionRequest.receiver_user_id == other_user_id)
        )
    )
    result = await db.execute(stmt)
    req = result.scalars().first()
    
    if not req:
        return ApiResponse(status=True, message="Pending request not found.", data=[])
        
    req.status = response
    
    if response == "accepted":
        low_id, high_id = build_connection_pair(req.sender_user_id, req.receiver_user_id)
        # Check if they are already connected before creating
        conn_check = select(Connection).where(Connection.user_low_id == low_id, Connection.user_high_id == high_id)
        conn_res = await db.execute(conn_check)
        existing_conn = conn_res.scalars().first()
        
        if existing_conn:
            existing_conn.is_active = True
        else:
            new_conn = Connection(user_low_id=low_id, user_high_id=high_id)
            db.add(new_conn)
            
    await db.commit()
    await db.refresh(req)

    # Queue email to the person who originally sent the request
    from apps.accounts.db_models import User
    from apps.profiles.db_models import Profile
    from core.email_service import send_lynkup_response_email
    import logging
    logger = logging.getLogger(__name__)
    logger.info("Attempting to send Lynkup response email for request %s", req.id)
    try:
        sender_stmt = select(User, Profile).join(Profile, Profile.user_id == User.id).where(User.id == req.sender_user_id)
        sender_result = await db.execute(sender_stmt)
        sender_row = sender_result.first()
        if sender_row:
            sender_user, sender_profile = sender_row
            full_name = f"{sender_profile.first_name} {sender_profile.last_name}".strip() if sender_profile else None
            await send_lynkup_response_email(sender_user.email, response, full_name)
            logger.info("Lynkup response email queued for %s", sender_user.email)
        else:
            logger.warning("Sender user not found for Lynkup response email, user_id=%s", req.sender_user_id)
    except Exception as e:
        logger.exception("Failed to queue Lynkup response email: %s", e)

    return ApiResponse(status=True, message=f"Request {response} successfully.", data=req)


async def follow_user(db: AsyncSession, follower_id: UUID, following_id: UUID) -> ApiResponse:
    if follower_id == following_id:
        return ApiResponse(status=True, message="Cannot follow self.", data=[])
    if await is_blocked(db, follower_id, following_id):
        return ApiResponse(status=True, message="Cannot follow blocked user.", data=[])
    # Check existing follow relationship
    stmt = select(Follow).where(Follow.follower_user_id == follower_id, Follow.following_user_id == following_id)
    result = await db.execute(stmt)
    existing_follow = result.scalars().first()
    async with db.begin():
        if existing_follow:
            if existing_follow.is_active:
                return ApiResponse(status=True, message="Already following.", data=[])
            existing_follow.is_active = True
            follow = existing_follow
            # Increment counts for reactivated follow
            await db.execute(update(Profile).where(Profile.user_id == following_id).values(followers_count=Profile.followers_count + 1))
            await db.execute(update(Profile).where(Profile.user_id == follower_id).values(following_count=Profile.following_count + 1))
        else:
            follow = Follow(follower_user_id=follower_id, following_user_id=following_id)
            db.add(follow)
            # Increment counts for new follow
            await db.execute(update(Profile).where(Profile.user_id == following_id).values(followers_count=Profile.followers_count + 1))
            await db.execute(update(Profile).where(Profile.user_id == follower_id).values(following_count=Profile.following_count + 1))
    await db.refresh(follow)
    return ApiResponse(status=True, message="Followed user successfully.", data=follow)


async def unfollow_user(db: AsyncSession, follower_id: UUID, following_id: UUID) -> ApiResponse:
    stmt = select(Follow).where(
        Follow.follower_user_id == follower_id,
        Follow.following_user_id == following_id,
        Follow.is_active == True
    )
    result = await db.execute(stmt)
    follow = result.scalars().first()
    if not follow:
        return ApiResponse(status=True, message="Follow not found.", data=[])
    async with db.begin():
        follow.is_active = False
        # Decrement counts, ensuring they stay non‑negative
        await db.execute(update(Profile).where(Profile.user_id == following_id).values(followers_count=Profile.followers_count - 1))
        await db.execute(update(Profile).where(Profile.user_id == follower_id).values(following_count=Profile.following_count - 1))
    return ApiResponse(status=True, message="Unfollowed user successfully.", data=[])


async def block_user(db: AsyncSession, blocker_id: UUID, blocked_id: UUID) -> ApiResponse:
    if blocker_id == blocked_id:
        return ApiResponse(status=True, message="Cannot block self.", data=[])
        
    # Check if already blocked
    stmt = select(Block).where(Block.blocker_user_id == blocker_id, Block.blocked_user_id == blocked_id)
    result = await db.execute(stmt)
    block = result.scalars().first()
    
    if block:
        if block.is_active:
            return ApiResponse(status=True, message="Already blocked.", data=[])
        block.is_active = True
    else:
        block = Block(blocker_user_id=blocker_id, blocked_user_id=blocked_id)
        db.add(block)
        
    # Remove connections
    low_id, high_id = build_connection_pair(blocker_id, blocked_id)
    conn_stmt = select(Connection).where(Connection.user_low_id == low_id, Connection.user_high_id == high_id)
    conn_result = await db.execute(conn_stmt)
    connection = conn_result.scalars().first()
    if connection:
        connection.is_active = False
        
    # Remove follows in BOTH directions
    follow_stmt = select(Follow).where(
        or_(
            and_(Follow.follower_user_id == blocker_id, Follow.following_user_id == blocked_id),
            and_(Follow.follower_user_id == blocked_id, Follow.following_user_id == blocker_id)
        )
    )
    follow_result = await db.execute(follow_stmt)
    for f in follow_result.scalars().all():
        f.is_active = False
        
    # Cancel pending requests
    req_stmt = select(ConnectionRequest).where(
        ConnectionRequest.status == "pending",
        or_(
            and_(ConnectionRequest.sender_user_id == blocker_id, ConnectionRequest.receiver_user_id == blocked_id),
            and_(ConnectionRequest.sender_user_id == blocked_id, ConnectionRequest.receiver_user_id == blocker_id)
        )
    )
    req_result = await db.execute(req_stmt)
    for req in req_result.scalars().all():
        req.status = "declined"
        
    await db.commit()
    await db.refresh(block)
    return ApiResponse(status=True, message="Blocked user successfully.", data=block)


async def unblock_user(db: AsyncSession, blocker_id: UUID, blocked_id: UUID) -> ApiResponse:
    stmt = select(Block).where(
        Block.blocker_user_id == blocker_id, 
        Block.blocked_user_id == blocked_id,
        Block.is_active == True
    )
    result = await db.execute(stmt)
    block = result.scalars().first()
    
    if not block:
        return ApiResponse(status=True, message="Block not found.", data=[])
        
    block.is_active = False
    await db.commit()
    return ApiResponse(status=True, message="Unblocked user successfully.", data=[])


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
    req_stmt = select(ConnectionRequest).where(
        ConnectionRequest.status == "pending",
        or_(ConnectionRequest.sender_user_id == user_id, ConnectionRequest.receiver_user_id == user_id)
    )
    req_res = await db.execute(req_stmt)
    pending_ids = set()
    for r in req_res.scalars().all():
        if r.sender_user_id == user_id:
            pending_ids.add(r.receiver_user_id)
        else:
            pending_ids.add(r.sender_user_id)
            
    # Combine excluded ids
    excluded_ids = current_connections.union(blocked_ids).union(pending_ids)
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


async def get_pending_requests(
    db: AsyncSession,
    user_id: UUID,
    page: int | None = None,
    page_size: int | None = None,
    search: str | None = None,
) -> ApiResponse:
    from common.pagination import paginate_items

    stmt = select(ConnectionRequest, Profile).join(
        Profile, Profile.user_id == ConnectionRequest.sender_user_id
    ).where(
        ConnectionRequest.receiver_user_id == user_id,
        ConnectionRequest.status == "pending"
    )
    
    if search:
        search_pattern = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                Profile.first_name.ilike(search_pattern),
                Profile.last_name.ilike(search_pattern)
            )
        )
        
    result = await db.execute(stmt)
    rows = result.all()
    
    data = [
        {
            "lynkup_id": req.id,
            "user_id": req.sender_user_id,
            "status": req.status,
            "first_name": profile.first_name,
            "last_name": profile.last_name,
            "profile_photo_key": profile.profile_photo_url,
        }
        for req, profile in rows
    ]
    
    if page is None and page_size is None:
        # if not provided: ALL
        return ApiResponse(status=True, message="Lynkup Request pending", data=data)
        
    p = page or 1
    ps = page_size or 20
    paginated = paginate_items(data, page=p, page_size=ps)
    return ApiResponse(status=True, message="Lynkup Request pending", data=paginated.model_dump())
