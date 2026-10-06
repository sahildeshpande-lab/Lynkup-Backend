"""Shared X-Client-Type header: RSA-OAEP decrypt → web | mobile routing.

FE encrypts plaintext (e.g. ``web``) with the server public key (SPKI base64).
BE decrypts with the matching private key and branches security middleware.
"""

from __future__ import annotations

import base64
import logging
from functools import lru_cache
from pathlib import Path
from typing import Final

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from fastapi import Request

from common.exceptions import ApiError
from core.auth.config import settings as auth_settings

logger = logging.getLogger(__name__)

HEADER_CLIENT_TYPE: Final = "X-Client-Type"
CLIENT_TYPE_WEB: Final = "web"
CLIENT_TYPE_MOBILE: Final = "mobile"
ALLOWED_CLIENT_TYPES: Final = frozenset({CLIENT_TYPE_WEB, CLIENT_TYPE_MOBILE})

GENERIC_CLIENT_TYPE_FAILURE = "Request authentication failed"


class ClientTypeError(ValueError):
    """Raised when X-Client-Type is missing, malformed, or unexpected."""


def _load_private_key_pem() -> str | None:
    raw = (auth_settings.client_type_rsa_private_key_pem or "").strip()
    if raw:
        return raw.replace("\\n", "\n")
    path = (auth_settings.client_type_rsa_private_key_path or "").strip()
    if not path:
        return None
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        logger.warning("CLIENT_TYPE_RSA_PRIVATE_KEY_PATH unreadable: %s", path)
        return None


@lru_cache(maxsize=1)
def _private_key() -> RSAPrivateKey | None:
    pem = _load_private_key_pem()
    if not pem:
        return None
    try:
        key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    except Exception:
        logger.exception("Failed to load CLIENT_TYPE RSA private key")
        return None
    if not isinstance(key, RSAPrivateKey):
        logger.error("CLIENT_TYPE key is not an RSA private key")
        return None
    return key


def clear_client_type_key_cache() -> None:
    """Test helper: drop cached private key after env/settings changes."""
    _private_key.cache_clear()


def client_type_enforcement_enabled() -> bool:
    """When true, missing/invalid X-Client-Type fails closed on protected deps."""
    if not auth_settings.client_type_enforce:
        return False
    return _private_key() is not None


def decrypt_client_type_ciphertext(ciphertext_b64: str) -> str:
    """Decrypt standard-base64 RSA-OAEP(SHA-256) ciphertext → UTF-8 plaintext."""
    key = _private_key()
    if key is None:
        raise ClientTypeError("Client-type private key is not configured")

    raw = (ciphertext_b64 or "").strip()
    if not raw:
        raise ClientTypeError("Missing client-type ciphertext")

    try:
        encrypted = base64.b64decode(raw, validate=False)
    except Exception as exc:
        raise ClientTypeError("Invalid client-type encoding") from exc

    try:
        plain = key.decrypt(
            encrypted,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
    except Exception as exc:
        raise ClientTypeError("Client-type decryption failed") from exc

    try:
        value = plain.decode("utf-8").strip().lower()
    except Exception as exc:
        raise ClientTypeError("Client-type plaintext is not UTF-8") from exc

    if value not in ALLOWED_CLIENT_TYPES:
        raise ClientTypeError(f"Unsupported client-type {value!r}")
    return value


def resolve_client_type(request: Request) -> str:
    """Read and decrypt ``X-Client-Type``; raise ApiError on failure when enforced."""
    if not client_type_enforcement_enabled():
        # Soft mode: accept plaintext web/mobile for local/tests without a key.
        raw = (request.headers.get(HEADER_CLIENT_TYPE) or "").strip().lower()
        if raw in ALLOWED_CLIENT_TYPES:
            return raw
        if not raw:
            return CLIENT_TYPE_WEB
        # Might still be ciphertext without a key configured — cannot decrypt.
        raise ApiError(GENERIC_CLIENT_TYPE_FAILURE)

    raw = (request.headers.get(HEADER_CLIENT_TYPE) or "").strip()
    if not raw:
        raise ApiError(GENERIC_CLIENT_TYPE_FAILURE)
    try:
        return decrypt_client_type_ciphertext(raw)
    except ClientTypeError:
        raise ApiError(GENERIC_CLIENT_TYPE_FAILURE)


def require_client_type(request: Request, *, allowed: frozenset[str] | set[str] | None = None) -> str:
    """Decrypt client type, optionally restrict to an allowlist, stash on request.state."""
    client_type = resolve_client_type(request)
    allowed_set = frozenset(allowed) if allowed is not None else ALLOWED_CLIENT_TYPES
    if client_type not in allowed_set:
        raise ApiError(GENERIC_CLIENT_TYPE_FAILURE)
    request.state.client_type = client_type
    return client_type


def require_web_client_type(request: Request) -> str:
    """Common dependency: decrypted client type must be ``web``."""
    return require_client_type(request, allowed={CLIENT_TYPE_WEB})


def require_mobile_client_type(request: Request) -> str:
    """Common dependency: decrypted client type must be ``mobile``."""
    return require_client_type(request, allowed={CLIENT_TYPE_MOBILE})
