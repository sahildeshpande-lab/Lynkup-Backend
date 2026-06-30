from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from apps.accounts.db_models import User
from apps.connections.db_models import Follow
from apps.profiles.db_models import Profile

from ..schemas import ApiResponse, FollowResponse
from .connection_service import is_blocked
from common.responses import error_response, success_response

logger = logging.getLogger(__name__)


async def follow_user(db: AsyncSession, follower_id: UUID, following_id: UUID) -> ApiResponse:
    if follower_id == following_id:
        return error_response("Cannot follow self.", response_cls=ApiResponse)

    target_user = await db.get(User, following_id)
    if not target_user or target_user.is_deleted:
        return error_response("User to follow not found.", response_cls=ApiResponse)

    if await is_blocked(db, follower_id, following_id):
        return error_response("Cannot follow blocked user.", response_cls=ApiResponse)

    stmt = select(Follow).where(
        Follow.follower_user_id == follower_id,
        Follow.following_user_id == following_id,
    )
    result = await db.execute(stmt)
    existing_follow = result.scalars().first()

    if existing_follow and existing_follow.is_active:
        return error_response(
            "Already following.",
            FollowResponse.model_validate(existing_follow),
            response_cls=ApiResponse,
            flags={"already_following": True},
        )

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
        stmt = select(Follow).where(
            Follow.follower_user_id == follower_id,
            Follow.following_user_id == following_id,
        )
        res = await db.execute(stmt)
        follow = res.scalars().first()
        if follow:
            return error_response(
                "Already following.",
                FollowResponse.model_validate(follow),
                response_cls=ApiResponse,
                flags={"already_following": True},
            )
        return error_response("Failed to follow user due to constraint violation.", response_cls=ApiResponse)
    except Exception:
        await db.rollback()
        logger.exception("Failed to follow user follower_id=%s following_id=%s", follower_id, following_id)
        return error_response("Failed to follow user.", response_cls=ApiResponse)

    return success_response("Followed user successfully.", FollowResponse.model_validate(follow), response_cls=ApiResponse)


async def unfollow_user(db: AsyncSession, follower_id: UUID, following_id: UUID) -> ApiResponse:
    stmt = select(Follow).where(
        Follow.follower_user_id == follower_id,
        Follow.following_user_id == following_id,
        Follow.is_active == True,
    )
    result = await db.execute(stmt)
    follow = result.scalars().first()
    if not follow:
        return error_response("Follow not found.", response_cls=ApiResponse)

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
    except Exception:
        await db.rollback()
        logger.exception("Failed to unfollow user follower_id=%s following_id=%s", follower_id, following_id)
        return error_response("Failed to unfollow user.", response_cls=ApiResponse)

    return success_response("Unfollowed user successfully.", [], response_cls=ApiResponse)
