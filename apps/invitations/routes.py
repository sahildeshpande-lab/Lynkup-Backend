from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from apps.invitations.schemas import (
    InvitationCreateResponse,
    InvitationValidateResponse,
    ValidateInvitationRequest,
)
from apps.invitations.services import (
    create_invitation,
    validate_invitation,
)
from core.database.session import get_session
from core.security.auth import get_current_app_user

router = APIRouter(tags=["8] Invitations"])


@router.post(
    "/invitations",
    response_model=InvitationCreateResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate an invitation code",
    description=(
        "Create a new invitation code for the authenticated user "
        "(format ABC1234: 3 letters + digits). "
        "Limited to INVITATION_DAILY_LIMIT codes per calendar day "
        "in INVITATION_TIMEZONE. Codes expire after INVITATION_BLOCK_DAYS."
    ),
)
async def generate_invitation_route(
    current_user: Annotated[User, Depends(get_current_app_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> InvitationCreateResponse:
    return await create_invitation(db, current_user.id)


@router.post(
    "/invitations/validate",
    response_model=InvitationValidateResponse,
    status_code=status.HTTP_200_OK,
    summary="Validate an invitation code",
    description=(
        "Validate that an invitation code exists, is active, unexpired, "
        "undeleted, and has not been redeemed."
    ),
)
async def validate_invitation_route(
    payload: ValidateInvitationRequest,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> InvitationValidateResponse:
    return await validate_invitation(db, payload.code)
