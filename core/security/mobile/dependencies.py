"""FastAPI dependencies for mobile request security.

Identity remains in ``core.security.auth`` (frozen). This module adds device
binding, rate limiting, platform attestation, and request proof orchestration.
"""

from __future__ import annotations

from typing import Collection

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from common.exceptions import ApiError
from core.database.session import get_session
from core.request_signing import CLIENT_TYPE_MOBILE
from core.security.auth import (
    AuthenticatedRequest,
    authenticate_request,
    bearer_scheme,
    get_current_user,
)
from core.security.mobile.device import MobileSecurityContext
from core.security.mobile.pipeline import run_mobile_security_pipeline
from core.security.mobile.request_proof import verify_mobile_request_proof


async def enforce_mobile_request_proof(
    request: Request,
    user: User,
) -> User:
    """Run mobile proof for an already-authenticated app user (compat helper)."""
    await verify_mobile_request_proof(request, user)
    return user


async def require_mobile_request_security(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    db: AsyncSession = Depends(get_session),
) -> User:
    """Full mobile pipeline for app-user-only endpoints.

    Firebase/JWT identity → device bind → rate limit → Integrity/Attest →
    timestamp/nonce/HMAC proof.

    Returns ``User`` for drop-in replacement of ``get_current_app_user`` /
    ``get_current_user`` on mobile routes. Context is on
    ``request.state.mobile_security``.
    """
    user = await get_current_user(credentials, db)
    if user.role != "user":
        raise ApiError("Insufficient permissions")
    await run_mobile_security_pipeline(request, user, db, include_proof=True)
    return user


async def require_mobile_security_context(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    db: AsyncSession = Depends(get_session),
) -> MobileSecurityContext:
    """Same as :func:`require_mobile_request_security` but returns the context."""
    user = await get_current_user(credentials, db)
    if user.role != "user":
        raise ApiError("Insufficient permissions")
    return await run_mobile_security_pipeline(request, user, db, include_proof=True)


async def _shared_authenticate_then_mobile(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
    db: AsyncSession,
    *,
    allowed_roles: Collection[str],
) -> User:
    """Shared web/mobile routes: ``authenticate_request`` then mobile extras."""
    auth: AuthenticatedRequest = await authenticate_request(
        request,
        credentials,
        db,
        allowed_roles=frozenset(allowed_roles),
    )
    if auth.client_type == CLIENT_TYPE_MOBILE:
        # Proof already ran inside authenticate_request → _verify_mobile_request_proof.
        await run_mobile_security_pipeline(
            request,
            auth.user,
            db,
            include_proof=False,
            proof_already_applied=True,
        )
    return auth.user


async def get_current_user_or_superadmin_secured(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    db: AsyncSession = Depends(get_session),
) -> User:
    """Shared routes: web RSA via authenticate_request; mobile full pipeline."""
    return await _shared_authenticate_then_mobile(
        request,
        credentials,
        db,
        allowed_roles={"user", "superadmin"},
    )


async def get_current_user_moderator_or_superadmin_secured(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    db: AsyncSession = Depends(get_session),
) -> User:
    """Shared routes with staff roles allowed for web admin path."""
    return await _shared_authenticate_then_mobile(
        request,
        credentials,
        db,
        allowed_roles={"user", "moderator", "viewer", "superadmin"},
    )
