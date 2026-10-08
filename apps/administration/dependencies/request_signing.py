"""FastAPI dependency for Web Admin RSA request signing."""

from __future__ import annotations

from fastapi import Depends, Request, Security
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from apps.administration.services.signing_service import (
    GENERIC_AUTH_FAILURE,
    HEADER_KEY_ID,
    HEADER_NONCE,
    HEADER_SESSION_ID,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    validate_origin,
    verify_signed_admin_request,
)
from common.exceptions import ApiError
from core.database.session import get_session
from core.request_signing import CLIENT_TYPE_WEB, mark_client_type
from core.security.auth import get_current_admin

# Documented in OpenAPI / Swagger Authorize. auto_error=False so missing
# headers are rejected by verify_signed_admin_request (generic auth message).
admin_key_id_header = APIKeyHeader(
    name=HEADER_KEY_ID,
    auto_error=False,
    scheme_name="AdminKeyId",
    description="Signing key UUID from key-register / login.",
)
admin_session_id_header = APIKeyHeader(
    name=HEADER_SESSION_ID,
    auto_error=False,
    scheme_name="AdminSessionId",
    description="Admin session UUID from login (must match JWT session).",
)
admin_timestamp_header = APIKeyHeader(
    name=HEADER_TIMESTAMP,
    auto_error=False,
    scheme_name="AdminTimestamp",
    description="Unix epoch seconds (within ADMIN_SIGNING_TIMESTAMP_TOLERANCE_SECONDS).",
)
admin_nonce_header = APIKeyHeader(
    name=HEADER_NONCE,
    auto_error=False,
    scheme_name="AdminNonce",
    description="Unique base64url nonce per request (replay protection).",
)
admin_signature_header = APIKeyHeader(
    name=HEADER_SIGNATURE,
    auto_error=False,
    scheme_name="AdminSignature",
    description="Base64 RSA-PSS/SHA-256 signature over the canonical request string.",
)


def require_admin_origin(request: Request) -> None:
    """Origin allowlist check — runs before JWT in the signed-request dependency graph."""
    validate_origin(request)


async def require_admin_signed_request(
    request: Request,
    _origin: None = Depends(require_admin_origin),
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
    _x_key_id: str | None = Security(admin_key_id_header),
    _x_session_id: str | None = Security(admin_session_id_header),
    _x_timestamp: str | None = Security(admin_timestamp_header),
    _x_nonce: str | None = Security(admin_nonce_header),
    _x_signature: str | None = Security(admin_signature_header),
) -> User:
    """Validate Origin, JWT+session, RSA signature, nonce.

    Client type is inferred as web from admin JWT + RSA — no X-Client-Type header.
    SESSION_ID for canonicalization comes only from the authenticated admin
    session on ``request.state`` — never from user.id or client input.

    The ``_x_*`` Security params exist so Swagger/OpenAPI expose the signing
    headers; verification still reads them from ``request.headers``.
    """
    mark_client_type(request, CLIENT_TYPE_WEB)
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
