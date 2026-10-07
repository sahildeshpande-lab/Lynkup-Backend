"""FastAPI dependency for Web Admin RSA request signing."""

from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from apps.administration.services.signing_service import (
    GENERIC_AUTH_FAILURE,
    verify_signed_admin_request,
)
from common.exceptions import ApiError
from core.database.session import get_session
from core.security.auth import get_current_admin


async def require_admin_signed_request(
    request: Request,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
) -> User:
    """Validate Origin, JWT+session (via get_current_admin), RSA signature, nonce.

    SESSION_ID for canonicalization comes only from the authenticated admin
    session on ``request.state`` — never from user.id or client input.
    """
    session_id = getattr(request.state, "admin_session_id", None)
    if session_id is None:
        raise ApiError(GENERIC_AUTH_FAILURE)
    return await verify_signed_admin_request(
        request,
        current_user,
        db,
        session_id=session_id,
    )
