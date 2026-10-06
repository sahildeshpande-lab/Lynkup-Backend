"""Email notification preference helpers.

These preferences gate optional marketing/digest emails only — not OTP,
password reset, or other mandatory transactional/security mail.

``weekly_lynkup_request_reminder`` lives under category_preferences (push/in-app),
not email_preferences.
"""

from __future__ import annotations

from typing import Any, Mapping

EMAIL_PREF_BULK_EMAIL = "bulk_email"

# Preference key exposed in category_preferences (not email). Kept here so
# callers that historically imported the email constant keep a single source.
CATEGORY_PREF_WEEKLY_LYNKUP_REQUEST_REMINDER = "weekly_lynkup_request_reminder"
# Backward-compatible alias for older imports.
EMAIL_PREF_WEEKLY_LYNKUP_REQUEST_REMINDER = CATEGORY_PREF_WEEKLY_LYNKUP_REQUEST_REMINDER

ALLOWED_EMAIL_PREFERENCE_KEYS = frozenset(
    {
        EMAIL_PREF_BULK_EMAIL,
    }
)

DEFAULT_EMAIL_PREFERENCES: dict[str, bool] = {
    EMAIL_PREF_BULK_EMAIL: True,
}

EXTRA_CATEGORY_PREFERENCE_DEFAULTS: dict[str, bool] = {
    CATEGORY_PREF_WEEKLY_LYNKUP_REQUEST_REMINDER: True,
}


def default_email_preferences() -> dict[str, bool]:
    """Return a fresh default email-preferences mapping."""
    return dict(DEFAULT_EMAIL_PREFERENCES)


def merge_email_preferences(stored: Mapping[str, Any] | None) -> dict[str, bool]:
    """Overlay stored values onto defaults; missing keys remain enabled."""
    merged = default_email_preferences()
    if not stored:
        return merged
    for key in ALLOWED_EMAIL_PREFERENCE_KEYS:
        if key in stored:
            merged[key] = bool(stored[key])
    return merged


def is_email_preference_enabled(
    preferences: Any | None,
    preference: str,
) -> bool:
    """Return whether ``preference`` is enabled (default True when missing)."""
    if not preferences:
        return True

    email_preferences = getattr(preferences, "email_preferences", None)
    if email_preferences is None and isinstance(preferences, Mapping):
        email_preferences = preferences

    if not email_preferences:
        return True

    return email_preferences.get(preference, True) is True
