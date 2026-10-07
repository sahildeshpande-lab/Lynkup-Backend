from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from apps.share.dependencies import require_share_post_token
from apps.share.schemas import ShareLinkRequest, ShareLinkResponse, SharePostRequest, SharePostResponse
from apps.share.service import get_shareable_post
from apps.share.services.share_link_service import create_link
from common.responses import success_response
from core.database.session import get_session
from core.security.auth import get_current_app_user

router = APIRouter(prefix="/share", tags=["Share"])


from core.security.mobile.dependencies import require_mobile_request_security
@router.post(
    "/link",
    response_model=ShareLinkResponse,
    summary="Create a Branch.io invite or post share link",
    description=(
        "Authenticated users can create an invitation deep link (type=invite) "
        "or a post share deep link (type=share). user_id is taken from the "
        "Firebase access token and is never accepted from the request body."
    ),
)
async def create_share_link(
    payload: ShareLinkRequest,
    current_user: Annotated[User, Depends(require_mobile_request_security)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ShareLinkResponse:
    return await create_link(db, current_user.id, payload)


@router.post(
    "/post",
    response_model=SharePostResponse,
    summary="Retrieve a post for public sharing",
    description=(
        "Public share endpoint authenticated only by ``X-Share-Token``. "
        "Pass the Branch share ``code`` from POST /share/link (type=share). "
        "Does not use Firebase or the current-user dependency."
    ),
    dependencies=[Depends(require_share_post_token)],
)
async def share_post(
    payload: SharePostRequest,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> SharePostResponse:
    data = await get_shareable_post(db, payload.code)
    return success_response(
        "Post retrieved successfully",
        data=data,
        response_cls=SharePostResponse,
    )
