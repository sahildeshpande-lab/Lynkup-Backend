from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from apps.share.dependencies import require_share_post_token
from apps.share.schemas import SharePostRequest, SharePostResponse
from apps.share.service import get_shareable_post
from common.responses import success_response
from core.database.session import get_session

router = APIRouter(prefix="/share", tags=["Share"])


@router.post(
    "/post",
    response_model=SharePostResponse,
    summary="Retrieve a post for public sharing",
    dependencies=[Depends(require_share_post_token)],
)
async def share_post(
    payload: SharePostRequest,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> SharePostResponse:
    """Public share endpoint authenticated only by ``X-Share-Token``.

    Does not use Firebase or the current-user dependency.
    """
    data = await get_shareable_post(db, payload.post_id)
    return success_response(
        "Post retrieved successfully",
        data=data,
        response_cls=SharePostResponse,
    )
