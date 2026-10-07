"""Per-device HMAC key provisioning (never a global app secret)."""

from __future__ import annotations

import base64
import secrets

from apps.accounts.db_models import UserInstallation


def generate_mobile_hmac_secret() -> str:
    """Return URL-safe base64 of 32 random bytes for one installation."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")


def decode_mobile_hmac_secret(stored: str | None) -> bytes | None:
    if not stored:
        return None
    raw = stored.strip()
    if not raw:
        return None
    pad = "=" * (-len(raw) % 4)
    try:
        return base64.urlsafe_b64decode(raw + pad)
    except Exception:
        try:
            return base64.b64decode(raw + pad)
        except Exception:
            return None


def hmac_key_from_installation(installation: UserInstallation | None) -> bytes | None:
    if installation is None:
        return None
    return decode_mobile_hmac_secret(getattr(installation, "mobile_hmac_secret", None))


def ensure_installation_hmac_secret(installation: UserInstallation) -> str:
    """Mint a per-device secret if missing; return the stored base64 value."""
    existing = (getattr(installation, "mobile_hmac_secret", None) or "").strip()
    if existing:
        return existing
    secret = generate_mobile_hmac_secret()
    installation.mobile_hmac_secret = secret
    return secret
