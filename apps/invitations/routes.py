from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from apps.invitations.schemas import (
    AssociateInvitationRequest,
    InvitationAssociateResponse,
    InvitationCreateResponse,
    InvitationRedeemResponse,
    InvitationValidateResponse,
    RedeemInvitationRequest,
    ValidateInvitationRequest,
)
from apps.invitations.services import (
    associate_invitation,
    create_invitation,
    redeem_invitation_response,
    validate_invitation,
)
from core.database.session import get_session
from core.security.auth import get_current_app_user

router = APIRouter(tags=["8] Invitations"])


from core.security.mobile.dependencies import require_mobile_request_security
@router.post(
    "/invitations",
    response_model=InvitationCreateResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate an invitation code",
    description=(
        "Create a new invitation code for the authenticated user "
        "(format ABC1234: 3 letters + digits). "
        "Limited to INVITATION_DAILY_LIMIT codes per calendar day "
        "in ANALYTICS_TIMEZONE. Codes expire after INVITATION_BLOCK_DAYS."
    ),
)
async def generate_invitation_route(
    current_user: Annotated[User, Depends(require_mobile_request_security)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> InvitationCreateResponse:
    return await create_invitation(db, current_user.id)


@router.post(
    "/invitations/associate",
    response_model=InvitationAssociateResponse,
    status_code=status.HTTP_200_OK,
    summary="Associate an invitation code",
    description=(
        "Store a frontend-provided invitation code against the authenticated user "
        "on invitations.redeemed_by_user_id. Limited to INVITATION_DAILY_LIMIT "
        "associations per calendar day in ANALYTICS_TIMEZONE. Codes expire after "
        "INVITATION_BLOCK_DAYS."
    ),
)
async def associate_invitation_route(
    payload: AssociateInvitationRequest,
    current_user: Annotated[User, Depends(require_mobile_request_security)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> InvitationAssociateResponse:
    return await associate_invitation(db, current_user.id, payload.code)


@router.post(
    "/invitations/validate",
    response_model=InvitationValidateResponse,
    status_code=status.HTTP_200_OK,
    summary="Validate an invitation code",
    description="Look up an invitation code and return its current status.",
)
async def validate_invitation_route(
    payload: ValidateInvitationRequest,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> InvitationValidateResponse:
    return await validate_invitation(db, payload.code)


@router.post(
    "/invitations/redeem",
    response_model=InvitationRedeemResponse,
    status_code=status.HTTP_200_OK,
    summary="Redeem an invitation code",
    description=(
        "Redeem a Branch-generated or legacy invitation code for the authenticated "
        "user during onboarding. user_id is taken from the Firebase access token."
    ),
)
async def redeem_invitation_route(
    payload: RedeemInvitationRequest,
    current_user: Annotated[User, Depends(require_mobile_request_security)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> InvitationRedeemResponse:
    return await redeem_invitation_response(
        db,
        code=payload.code,
        redeemed_by_user_id=current_user.id,
    )
