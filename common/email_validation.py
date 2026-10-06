"""Shared email validation helpers (disposable-domain checks, etc.)."""

from __future__ import annotations

from disposable_email_domains import blocklist

from common.exceptions import ApiError

DISPOSABLE_EMAIL_NOT_ALLOWED_MESSAGE = "Disposable email addresses are not allowed."


def validate_disposable_email(email: str, *, is_enabled: bool) -> None:
    """Reject known disposable email domains when the feature flag is enabled.

    When ``is_enabled`` is False, this is a no-op so existing registration
    behavior is unchanged.

    Raises
    ------
    ApiError
        If the feature is enabled and the email's domain is on the maintained
        disposable-email blocklist, or the email has no usable domain part.
    """
    if not is_enabled:
        return

    raw = (email or "").strip()
    if "@" not in raw:
        raise ApiError(DISPOSABLE_EMAIL_NOT_ALLOWED_MESSAGE)

    _, _, domain = raw.rpartition("@")
    domain = domain.strip().casefold()
    if not domain:
        raise ApiError(DISPOSABLE_EMAIL_NOT_ALLOWED_MESSAGE)

    if domain in blocklist:
        raise ApiError(DISPOSABLE_EMAIL_NOT_ALLOWED_MESSAGE)
