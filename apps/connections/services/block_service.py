from __future__ import annotations
from uuid import UUID
from sqlalchemy import or_, and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from apps.connections.db_models import Block, Connection, ConnectionRequest, Follow
from fastapi import HTTPException, status
from ..schemas import ApiResponse, BlockResponse

from .connection_service import build_connection_pair

async def block_user(db: AsyncSession, blocker_id: UUID, blocked_id: UUID) -> ApiResponse:
    if blocker_id == blocked_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot block self.")

    # Check if already blocked
    stmt = select(Block).where(Block.blocker_user_id == blocker_id, Block.blocked_user_id == blocked_id)
    result = await db.execute(stmt)
    block = result.scalars().first()

    if block:
        if block.is_active:
            return ApiResponse(status=True, message="Already blocked.",  flags={
        "already_blocked": True
    }, data=BlockResponse.model_validate(block))
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

    try:
        await db.commit()
        await db.refresh(block)
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to block user: {str(e)}")

    return ApiResponse(status=True, message="Blocked user successfully.", data=BlockResponse.model_validate(block))

async def unblock_user(db: AsyncSession, blocker_id: UUID, blocked_id: UUID) -> ApiResponse:
    stmt = select(Block).where(
        Block.blocker_user_id == blocker_id,
        Block.blocked_user_id == blocked_id,
        Block.is_active == True
    )
    result = await db.execute(stmt)
    block = result.scalars().first()

    if not block:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Block not found.")

    try:
        block.is_active = False
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to unblock user: {str(e)}")

    return ApiResponse(status=True, message="Unblocked user successfully.", data=[])
