# core/security/admin.py
"""Admin‑session cookie authentication utilities.

Provides:
- `create_admin_session_cookie` endpoint – receives a Firebase ID token, creates a
  secure HttpOnly SameSite=Strict cookie.
- `get_current_admin_user` dependency – validates the session cookie and returns
  the corresponding ``User``.
- Simple CSRF token generation (UUID) returned in the response body.
"""

from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin import auth as firebase_auth

from core.db.session import get_session
from core.auth.services import verify_firebase_token
from apps.accounts.db_models import User
from apps.accounts.services import get_user_by_firebase_uid

router = APIRouter(prefix="/auth", tags=["Admin Session"])

# ---------------------------------------------------------------------------
# Helper – create session cookie
# ---------------------------------------------------------------------------

@router.post("/admin-cookie", response_model=dict)
async def create_admin_session_cookie(
    credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer(auto_error=False)),
    response: Response = None,
) -> dict:
    """Create a Firebase session cookie for admin browsers.

    The client sends a Firebase ID token (obtained via sign‑in). The backend
    verifies the token, creates a session cookie (default 5 days) and returns a
    CSRF token that the client must send back on subsequent state‑changing
    requests.
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing ID token"
        )
    # Verify token (including revocation check)
    decoded = verify_firebase_token(credentials.credentials, check_revoked=True)
    # Ensure the user has the admin/superadmin role
    if decoded.get("role") not in ("admin", "superadmin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
        )
    # Ensure MFA is completed for admin accounts
    firebase_claims = decoded.get("firebase", {})
    if not firebase_claims.get("sign_in_second_factor"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="MFA required"
        )
    # Create a session cookie (5 days = 432000 seconds)
    expires_in = 5 * 24 * 60 * 60
    session_cookie = firebase_auth.create_session_cookie(
        credentials.credentials, expires_in=expires_in
    )
    # Set cookie – Secure, HttpOnly, SameSite=Strict
    cookie_name = "admin_session"
    response.set_cookie(
        key=cookie_name,
        value=session_cookie,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=expires_in,
        path="/",
    )
    # Return a CSRF token (client must send it as header "X‑CSRF‑Token")
    csrf_token = str(uuid.uuid4())
    _csrf_store[cookie_name + ":" + session_cookie] = csrf_token
    return {"csrf_token": csrf_token}

# ---------------------------------------------------------------------------
# Dependency – verify session cookie & CSRF
# ---------------------------------------------------------------------------

_csrf_store: dict[str, str] = {}

async def get_current_admin_user(
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> User:
    """Validate the admin session cookie and return the associated ``User``.

    The client must include the ``X‑CSRF‑Token`` header matching the token
    issued when the cookie was created.
    """
    cookie = request.cookies.get("admin_session")
    if not cookie:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing admin session"
        )
    try:
        decoded = firebase_auth.verify_session_cookie(cookie, check_revoked=True)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin session"
        ) from exc
    # CSRF validation
    csrf_header = request.headers.get("X-CSRF-Token")
    expected_csrf = _csrf_store.get("admin_session:" + cookie)
    if not expected_csrf or csrf_header != expected_csrf:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token"
        )
    # Load local user
    firebase_uid = decoded["uid"]
    user = await get_user_by_firebase_uid(db, firebase_uid)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    # Ensure admin/superadmin role
    if user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return user
