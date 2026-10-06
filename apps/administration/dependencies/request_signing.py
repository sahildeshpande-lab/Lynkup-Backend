"""FastAPI dependency for Web Admin RSA request signing."""

from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from apps.administration.services.signing_service import (
    GENERIC_AUTH_FAILURE,
    validate_origin,
    verify_signed_admin_request,
)
from common.exceptions import ApiError
from core.database.session import get_session
from core.request_signing import require_web_client_type
from core.security.auth import get_current_admin


def require_admin_origin(request: Request) -> None:
    """Origin allowlist check — runs before JWT in the signed-request dependency graph."""
    validate_origin(request)


async def require_admin_signed_request(
    request: Request,
    _client_type: str = Depends(require_web_client_type),
    _origin: None = Depends(require_admin_origin),
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
) -> User:
    """Validate client-type (web), Origin, JWT+session, RSA signature, nonce.

    SESSION_ID for canonicalization comes only from the authenticated admin
    session on ``request.state`` — never from user.id or client input.
    """
    _ = _client_type
    session_id = getattr(request.state, "admin_session_id", None)
    if session_id is None:
        raise ApiError(GENERIC_AUTH_FAILURE)
    return await verify_signed_admin_request(
        request,
        current_user,
        db,
        session_id=session_id,
        skip_origin=True,
    )
