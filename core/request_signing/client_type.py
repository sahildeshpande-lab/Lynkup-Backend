"""Client type derived from auth material — not from an X-Client-Type header.

Web admin: app JWT (type=access + session_id) + RSA-PSS request signature.
Mobile: Firebase (or app-user JWT) — HMAC request signing when enabled.
"""

from __future__ import annotations

from typing import Final

from fastapi import Request

CLIENT_TYPE_WEB: Final = "web"
CLIENT_TYPE_MOBILE: Final = "mobile"
ALLOWED_CLIENT_TYPES: Final = frozenset({CLIENT_TYPE_WEB, CLIENT_TYPE_MOBILE})


def mark_client_type(request: Request, client_type: str) -> str:
    """Record inferred client type on ``request.state`` for downstream handlers."""
    if client_type not in ALLOWED_CLIENT_TYPES:
        raise ValueError(f"unsupported client_type: {client_type}")
    request.state.client_type = client_type
    return client_type
