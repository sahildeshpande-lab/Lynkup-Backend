from __future__ import annotations
from uuid import UUID
from sqlalchemy import or_, and_, select, update, func
from sqlalchemy.ext.asyncio import AsyncSession
from apps.connections.db_models import Block, Connection, ConnectionRequest, Follow
from apps.profiles.db_models import Profile
from ..schemas import ApiResponse, BlockResponse
from common.responses import error_response, success_response

from .connection_service import build_connection_pair

async def block_user(db: AsyncSession, blocker_id: UUID, blocked_id: UUID) -> ApiResponse:
    if blocker_id == blocked_id:
        return error_response("Cannot block self.", response_cls=ApiResponse)

    stmt = select(Block).where(Block.blocker_user_id == blocker_id, Block.blocked_user_id == blocked_id)
    result = await db.execute(stmt)
    block = result.scalars().first()

    if block:
        if block.is_active:
            return error_response("Already blocked.", response_cls=ApiResponse)
        block.is_active = True
    else:
        block = Block(blocker_user_id=blocker_id, blocked_user_id=blocked_id)
        db.add(block)

    low_id, high_id = build_connection_pair(blocker_id, blocked_id)
    conn_stmt = select(Connection).where(Connection.user_low_id == low_id, Connection.user_high_id == high_id)
    conn_result = await db.execute(conn_stmt)
    connection = conn_result.scalars().first()
    if connection:
        connection.is_active = False

    follow_stmt = select(Follow).where(
        or_(
            and_(Follow.follower_user_id == blocker_id, Follow.following_user_id == blocked_id),
            and_(Follow.follower_user_id == blocked_id, Follow.following_user_id == blocker_id)
        )
    )
    follow_result = await db.execute(follow_stmt)
    for f in follow_result.scalars().all():
        if f.is_active:
            f.is_active = False
            await db.execute(
                update(Profile)
                .where(Profile.user_id == f.following_user_id)
                .values(followers_count=func.greatest(Profile.followers_count - 1, 0))
            )
            await db.execute(
                update(Profile)
                .where(Profile.user_id == f.follower_user_id)
                .values(following_count=func.greatest(Profile.following_count - 1, 0))
            )

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

    try:
        await db.commit()
        await db.refresh(block)
    except Exception:
        await db.rollback()
        return error_response("Failed to block user.", response_cls=ApiResponse)

    return success_response("Blocked user successfully.", BlockResponse.model_validate(block), response_cls=ApiResponse)

async def unblock_user(db: AsyncSession, blocker_id: UUID, blocked_id: UUID) -> ApiResponse:
    stmt = select(Block).where(
        Block.blocker_user_id == blocker_id,
        Block.blocked_user_id == blocked_id,
        Block.is_active == True
    )
    result = await db.execute(stmt)
    block = result.scalars().first()

    if not block:
        return error_response("Block not found.", response_cls=ApiResponse)

    try:
        block.is_active = False
        await db.commit()
    except Exception:
        await db.rollback()
        return error_response("Failed to unblock user.", response_cls=ApiResponse)

    return success_response("Unblocked user successfully.", [], response_cls=ApiResponse)
