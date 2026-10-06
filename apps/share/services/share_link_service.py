from __future__ import annotations

import logging
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from apps.engagement.repositories.share_repository import (
    create_share_event,
    get_share_event_for_post,
    get_user_share_event,
    update_post_share_count,
)
from apps.feed.db_models import Post, PostAttachment
from apps.invitations.config import INVITATION_CODE_MAX_LENGTH, settings as invitation_settings
from apps.invitations.repositories import (
    count_invitations_created_by_user_between,
    create_invitation as persist_invitation,
    invitation_code_exists,
)
from apps.invitations.services.invitation_service import (
    DAILY_LIMIT_MESSAGE,
    lock_invitation_creation_daily_limit,
    utc_day_bounds,
)
from apps.share.schemas import ShareLinkData, ShareLinkRequest, ShareLinkResponse, ShareLinkType
from apps.share.services.branch_service import BranchLinkError, create_branch_link
from apps.profiles.db_models import Profile
from common.enums import FEED_VISIBLE_POST_STATES, InvitationStatus, MediaType
from common.exceptions import ApiError
from common.responses import error_response, success_response
from common.time import utc_now
from common.user_visibility import check_post_engagement_allowed
from core.images import generate_download_url

logger = logging.getLogger(__name__)

INVITE_SUCCESS_MESSAGE = "Invitation link created successfully"
SHARE_SUCCESS_MESSAGE = "Share link created successfully"
BRANCH_FAILURE_MESSAGE = "Failed to create Branch link"
POST_NOT_FOUND_MESSAGE = "Post not found"




def _invite_branch_data(user_id) -> dict:
    identifier = uuid4()

    return {
        "$canonical_identifier": f"invite/{identifier}",
        "$deeplink_path": "invite",
        "type": ShareLinkType.invite.value,
        "referred_id": str(user_id),
    }


def _share_branch_data(
    code: str,
    post_id: UUID,
    first_name: str,
    post_image_url: str | None = None,
) -> dict:
    post_id_str = str(post_id)

    return {
        "$canonical_identifier": f"share/{code}",
        "$deeplink_path": f"post/{post_id_str}",
        "type": ShareLinkType.share.value,
        "post_id": post_id_str,

        "$og_title": "Check out this post on KampuLynk",
        "$og_description": (
            f"{first_name} shared a post on KampuLynk. "
            "Check it out and join the conversation."
        ),
        "$og_image_url": post_image_url,
        "$desktop_url": (
            f"https://kampulynk-stage-portal-ponyy.ondigitalocean.app/"
            f"post/{code}"
        ),
    }


async def _sharer_first_name(db: AsyncSession, user_id: UUID) -> str:
    stmt = select(Profile.first_name).where(Profile.user_id == user_id)
    raw = (await db.execute(stmt)).scalar_one_or_none()
    name = (str(raw).strip() if raw else "")
    return name or "Someone"


def _post_share_image_url(post: Post) -> str | None:
    attachments = getattr(post, "attachments", None) or []
    for attachment in attachments:
        asset = getattr(attachment, "media_asset", None)
        if asset is None:
            continue
        raw_type = getattr(asset, "type", None)
        type_val = raw_type.value if hasattr(raw_type, "value") else str(raw_type or "")
        if type_val.lower() not in {MediaType.image.value, MediaType.gif.value}:
            continue
        key = getattr(asset, "key", None)
        if key:
            return generate_download_url(key)
    return None


def _link_response(
    message: str,
    link_type: ShareLinkType,
    *,
    code: str,
    url: str,
) -> ShareLinkResponse:
    return success_response(
        message,
        ShareLinkData(type=link_type, code=code, url=url),
        response_cls=ShareLinkResponse,
    )


async def create_link(
    db: AsyncSession,
    user_id: UUID,
    payload: ShareLinkRequest,
) -> ShareLinkResponse:
    if payload.type == ShareLinkType.invite:
        return await create_invitation_link(db, user_id)
    return await create_post_share_link(db, user_id, payload.post_id)


async def _get_shareable_post(db: AsyncSession, post_id: UUID) -> Post | None:
    stmt = (
        select(Post)
        .where(
            Post.id == post_id,
            Post.state.in_(FEED_VISIBLE_POST_STATES),
        )
        .options(selectinload(Post.attachments).selectinload(PostAttachment.media_asset))
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _upsert_share_event(
    db: AsyncSession,
    user_id: UUID,
    post_id: UUID,
    *,
    branch_code: str,
    branch_url: str,
) -> None:
    now = utc_now()
    existing = await get_user_share_event(db, user_id, post_id)
    if existing is None:
        await create_share_event(
            db,
            user_id,
            post_id,
            branch_code=branch_code,
            branch_url=branch_url,
            now=now,
        )
        return
    existing.branch_code = branch_code
    existing.branch_url = branch_url
    existing.updated_at = now
    db.add(existing)


async def create_invitation_link(
    db: AsyncSession,
    user_id: UUID,
) -> ShareLinkResponse:
    now = utc_now()
    day_start, day_end = utc_day_bounds(now)
    await lock_invitation_creation_daily_limit(db, user_id)

    created_today = await count_invitations_created_by_user_between(
        db,
        user_id,
        start_at=day_start,
        end_at=day_end,
    )
    if created_today >= invitation_settings.daily_limit:
        logger.info(
            "Invite link daily limit reached user_id=%s created_today=%s limit=%s",
            user_id,
            created_today,
            invitation_settings.daily_limit,
        )
        await db.rollback()
        return error_response(DAILY_LIMIT_MESSAGE, response_cls=ShareLinkResponse)

    try:
        branch = await create_branch_link(_invite_branch_data(user_id))
    except BranchLinkError:
        logger.warning("Invite link Branch create failed user_id=%s", user_id)
        await db.rollback()
        return error_response(BRANCH_FAILURE_MESSAGE, response_cls=ShareLinkResponse)

    if not branch.code or len(branch.code) > INVITATION_CODE_MAX_LENGTH:
        logger.warning(
            "Invite link rejected invalid Branch code user_id=%s code=%s code_len=%s url=%s",
            user_id,
            branch.code,
            len(branch.code or ""),
            branch.url,
        )
        await db.rollback()
        return error_response(BRANCH_FAILURE_MESSAGE, response_cls=ShareLinkResponse)

    if await invitation_code_exists(db, branch.code):
        logger.warning(
            "Invite link rejected duplicate Branch code user_id=%s code=%s url=%s",
            user_id,
            branch.code,
            branch.url,
        )
        await db.rollback()
        return error_response(BRANCH_FAILURE_MESSAGE, response_cls=ShareLinkResponse)

    expires_at = now + timedelta(days=invitation_settings.block_days)
    try:
        invitation = await persist_invitation(
            db,
            inviter_user_id=user_id,
            code=branch.code,
            expires_at=expires_at,
            status=InvitationStatus.active,
        )
        await db.commit()
        await db.refresh(invitation)
    except IntegrityError:
        logger.warning(
            "Invite link persist integrity error user_id=%s code=%s url=%s",
            user_id,
            branch.code,
            branch.url,
        )
        await db.rollback()
        return error_response(BRANCH_FAILURE_MESSAGE, response_cls=ShareLinkResponse)
    except Exception:
        await db.rollback()
        logger.exception("Failed to persist invitation link user_id=%s", user_id)
        return error_response("Failed to create invitation link", response_cls=ShareLinkResponse)

    logger.info(
        "Invite link created user_id=%s code=%s url=%s",
        user_id,
        invitation.code,
        branch.url,
    )

    return _link_response(
        INVITE_SUCCESS_MESSAGE,
        ShareLinkType.invite,
        code=invitation.code,
        url=branch.url,
    )


def _has_branch_link(event) -> bool:
    return bool(
        event is not None
        and getattr(event, "branch_code", None)
        and getattr(event, "branch_url", None)
    )


async def _persist_share_link(
    db: AsyncSession,
    user_id: UUID,
    post_id: UUID,
    *,
    branch_code: str,
    branch_url: str,
) -> ShareLinkResponse | None:
    try:
        await _upsert_share_event(
            db,
            user_id,
            post_id,
            branch_code=branch_code,
            branch_url=branch_url,
        )
        await update_post_share_count(db, post_id, 1)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        try:
            await _upsert_share_event(
                db,
                user_id,
                post_id,
                branch_code=branch_code,
                branch_url=branch_url,
            )
            await update_post_share_count(db, post_id, 1)
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception(
                "Failed to persist share link after conflict user_id=%s post_id=%s",
                user_id,
                post_id,
            )
            return error_response("Failed to create share link", response_cls=ShareLinkResponse)
    except Exception:
        await db.rollback()
        logger.exception("Failed to persist share link user_id=%s post_id=%s", user_id, post_id)
        return error_response("Failed to create share link", response_cls=ShareLinkResponse)
    return None


async def create_post_share_link(
    db: AsyncSession,
    user_id: UUID,
    post_id: UUID | None,
) -> ShareLinkResponse:
    if post_id is None:
        return error_response(
            "post_id is required when type is share",
            response_cls=ShareLinkResponse,
        )

    post = await _get_shareable_post(db, post_id)
    if post is None:
        return error_response(POST_NOT_FOUND_MESSAGE, response_cls=ShareLinkResponse)

    author_id = getattr(post, "author_user_id", None)
    if author_id is not None:
        try:
            await check_post_engagement_allowed(db, user_id, author_id)
        except ApiError as exc:
            return error_response(exc.message, response_cls=ShareLinkResponse)

    existing_for_user = await get_user_share_event(db, user_id, post_id)
    if _has_branch_link(existing_for_user):
        return _link_response(
            SHARE_SUCCESS_MESSAGE,
            ShareLinkType.share,
            code=existing_for_user.branch_code,
            url=existing_for_user.branch_url,
        )

    existing_for_post = await get_share_event_for_post(db, post_id)
    if _has_branch_link(existing_for_post):
        persist_error = await _persist_share_link(
            db,
            user_id,
            post_id,
            branch_code=existing_for_post.branch_code,
            branch_url=existing_for_post.branch_url,
        )
        if persist_error is not None:
            return persist_error
        return _link_response(
            SHARE_SUCCESS_MESSAGE,
            ShareLinkType.share,
            code=existing_for_post.branch_code,
            url=existing_for_post.branch_url,
        )

    share_code = str(uuid4())
    first_name = await _sharer_first_name(db, user_id)
    try:
        branch = await create_branch_link(
            _share_branch_data(
                share_code,
                post_id,
                first_name,
                _post_share_image_url(post),
            ),
            alias=share_code,
        )
    except BranchLinkError:
        logger.warning(
            "Share link Branch create failed user_id=%s post_id=%s",
            user_id,
            post_id,
        )
        return error_response(BRANCH_FAILURE_MESSAGE, response_cls=ShareLinkResponse)

    persist_error = await _persist_share_link(
        db,
        user_id,
        post_id,
        branch_code=share_code,
        branch_url=branch.url,
    )
    if persist_error is not None:
        return persist_error

    return _link_response(
        SHARE_SUCCESS_MESSAGE,
        ShareLinkType.share,
        code=share_code,
        url=branch.url,
    )
