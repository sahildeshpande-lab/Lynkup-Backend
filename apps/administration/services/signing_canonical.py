"""Canonical request serialization for Web Admin RSA-PSS request signing.

Frontend and backend MUST produce identical UTF-8 bytes before signing/verifying.

Canonical form (LF newlines, no trailing newline after KEY_ID)::

    HTTP_METHOD
    REQUEST_PATH
    QUERY_STRING
    BODY_HASH
    TIMESTAMP
    NONCE
    SESSION_ID
    KEY_ID

Rules
-----
* HTTP_METHOD: uppercase ASCII (``GET``, ``POST``, …).
* REQUEST_PATH: exact API path including mount prefix (e.g. ``/api/v1/me``).
  No scheme/host. No trailing slash normalization beyond what the client sent
  as the request path.
* QUERY_STRING: deterministic serialization of query parameters:
  - Decode each name/value with UTF-8 (leave ``+`` as space via unquote_plus).
  - Sort by name ascending, then by value ascending (stable lexicographic).
  - Re-encode each name/value with ``urllib.parse.quote(s, safe="")``.
  - Join as ``name=value`` pairs with ``&``.
  - Empty query → empty string (not omitted).
* BODY_HASH: lowercase hex SHA-256 of the **raw request body bytes**.
  Empty body → SHA-256 of ``b""``
  (``e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855``).
  Do not re-serialize JSON on either side; hash the bytes actually transmitted.
* TIMESTAMP: unix epoch seconds as a decimal integer string (no milliseconds).
* NONCE: the exact base64url nonce string from ``X-Nonce`` (no re-encoding).
* SESSION_ID: independent admin session UUID from the authenticated JWT /
  ``admin_sessions.id`` (never ``user.id``, never the refresh token).
* KEY_ID: signing key id as UUID string.

Delimiter between the eight components is a single ``\\n`` (0x0A).
"""

from __future__ import annotations

import hashlib
from urllib.parse import parse_qsl, quote, urlencode


EMPTY_BODY_SHA256 = hashlib.sha256(b"").hexdigest()


def normalize_http_method(method: str) -> str:
    return (method or "").strip().upper()


def normalize_query_string(query_string: str | None) -> str:
    """Return a deterministically ordered query string.

    Accepts the raw query component without a leading ``?``.
    """
    raw = (query_string or "").lstrip("?")
    if not raw:
        return ""
    pairs = parse_qsl(raw, keep_blank_values=True)
    pairs.sort(key=lambda item: (item[0], item[1]))
    return urlencode(pairs, quote_via=quote, safe="")


def hash_request_body(body: bytes | None) -> str:
    return hashlib.sha256(body or b"").hexdigest()


def build_canonical_request(
    *,
    method: str,
    path: str,
    query_string: str | None,
    body: bytes | None,
    timestamp: str | int,
    nonce: str,
    session_id: str,
    key_id: str,
) -> str:
    """Build the canonical string that FE signs and BE verifies."""
    components = [
        normalize_http_method(method),
        path or "",
        normalize_query_string(query_string),
        hash_request_body(body),
        str(timestamp).strip(),
        (nonce or "").strip(),
        (session_id or "").strip(),
        (key_id or "").strip(),
    ]
    return "\n".join(components)


def canonical_request_bytes(**kwargs) -> bytes:
    return build_canonical_request(**kwargs).encode("utf-8")
