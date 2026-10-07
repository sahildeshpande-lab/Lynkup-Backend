"""Shared request-signing helpers (client-type tagging, future HMAC, etc.)."""

from core.request_signing.client_type import (
    ALLOWED_CLIENT_TYPES,
    CLIENT_TYPE_MOBILE,
    CLIENT_TYPE_WEB,
    mark_client_type,
)

__all__ = [
    "ALLOWED_CLIENT_TYPES",
    "CLIENT_TYPE_MOBILE",
    "CLIENT_TYPE_WEB",
    "mark_client_type",
]
