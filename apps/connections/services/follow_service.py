from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from apps.connections.db_models import Follow
from apps.profiles.db_models import Profile

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from ..schemas import ApiResponse, FollowResponse
from .connection_service import is_blocked

logger = logging.getLogger(__name__)


async def follow_user(db: AsyncSession, follower_id: UUID, following_id: UUID) -> ApiResponse:
    if follower_id == following_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot follow self.")

    target_user = await db.get(User, following_id)
    if not target_user or target_user.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User to follow not found.")

    if await is_blocked(db, follower_id, following_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot follow blocked user.")

    stmt = select(Follow).where(
        Follow.follower_user_id == follower_id,
        Follow.following_user_id == following_id,
    )
    result = await db.execute(stmt)
    existing_follow = result.scalars().first()

    if existing_follow and existing_follow.is_active:
        return ApiResponse(status=True, message="Already following.", flags={
        "already_following": True
    }, data=FollowResponse.model_validate(existing_follow))

    try:
        if existing_follow:
            existing_follow.is_active = True
            follow = existing_follow
        else:
            follow = Follow(follower_user_id=follower_id, following_user_id=following_id)
            db.add(follow)

        await db.execute(
            update(Profile)
            .where(Profile.user_id == following_id)
            .values(followers_count=Profile.followers_count + 1)
        )
        await db.execute(
            update(Profile)
            .where(Profile.user_id == follower_id)
            .values(following_count=Profile.following_count + 1)
        )
        await db.commit()
        await db.refresh(follow)
    except IntegrityError as e:
        await db.rollback()
        logger.warning("Duplicate follow attempt follower_id=%s following_id=%s: %s", follower_id, following_id, e)
        # Fetch the existing one and return it
        stmt = select(Follow).where(
            Follow.follower_user_id == follower_id,
            Follow.following_user_id == following_id,
        )
        res = await db.execute(stmt)
        follow = res.scalars().first()
        if follow:
            return ApiResponse(status=True, message="Already following.", data=FollowResponse.model_validate(follow))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Failed to follow user due to constraint violation.")
    except Exception as e:
        await db.rollback()
        logger.exception("Failed to follow user follower_id=%s following_id=%s", follower_id, following_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to follow user: {str(e)}")

    return ApiResponse(status=True, message="Followed user successfully.", data=FollowResponse.model_validate(follow))


async def unfollow_user(db: AsyncSession, follower_id: UUID, following_id: UUID) -> ApiResponse:
    stmt = select(Follow).where(
        Follow.follower_user_id == follower_id,
        Follow.following_user_id == following_id,
        Follow.is_active == True,
    )
    result = await db.execute(stmt)
    follow = result.scalars().first()
    if not follow:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Follow not found.")

    try:
        follow.is_active = False
        await db.execute(
            update(Profile)
            .where(Profile.user_id == following_id)
            .values(followers_count=func.greatest(Profile.followers_count - 1, 0))
        )
        await db.execute(
            update(Profile)
            .where(Profile.user_id == follower_id)
            .values(following_count=func.greatest(Profile.following_count - 1, 0))
        )
        await db.commit()
    except Exception as e:
        await db.rollback()
        logger.exception("Failed to unfollow user follower_id=%s following_id=%s", follower_id, following_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to unfollow user: {str(e)}")

    return ApiResponse(status=True, message="Unfollowed user successfully.", data=[])
