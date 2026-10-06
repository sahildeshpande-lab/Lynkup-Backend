"""Name validation for academic catalog fields (major, minor, interest, country, university)."""

from __future__ import annotations

import re

_LETTER_RE = re.compile(r"[A-Za-z]")
_MAJOR_MINOR_INTEREST_ALLOWED_RE = re.compile(r"^[A-Za-z0-9 &\-]+$")
_COUNTRY_ALLOWED_RE = re.compile(r"^[A-Za-z \-]+$")
_UNIVERSITY_ALLOWED_RE = re.compile(r"^[A-Za-z0-9 \-'.&]+$")


def _letter_count(value: str) -> int:
    return len(_LETTER_RE.findall(value))


def validate_major_minor_interest_name(value: str, *, field: str = "name") -> str:
    """Validate major, minor, or academic-interest names.

    Allowed: letters, digits, spaces, hyphens, and ampersands.
    Must include at least one letter (rejects digits-only / symbols-only).
    """
    text = " ".join((value or "").split())
    if not text:
        raise ValueError(f"{field} cannot be empty")

    letters = _letter_count(text)
    if letters < 1:
        raise ValueError(
            f"{field} must contain at least one letter "
            "(cannot be only numbers or special characters)"
        )

    if not _MAJOR_MINOR_INTEREST_ALLOWED_RE.fullmatch(text):
        raise ValueError(
            f"{field} may only contain letters, numbers, spaces, hyphens (-), and ampersands (&)"
        )
    return text


def validate_country_name(value: str, *, field: str = "name") -> str:
    """Validate country names.

    Allowed: letters, spaces, and hyphens only.
    Must include at least two letters.
    """
    text = " ".join((value or "").split())
    if not text:
        raise ValueError(f"{field} cannot be empty")

    if _letter_count(text) < 2:
        raise ValueError(f"{field} must contain at least 2 letters")

    if not _COUNTRY_ALLOWED_RE.fullmatch(text):
        raise ValueError(
            f"{field} may only contain letters, spaces, and hyphens (-) "
            "(numbers and special characters are not allowed)"
        )
    return text


def validate_university_name(value: str, *, field: str = "name") -> str:
    """Validate university names.

    Allowed: letters, digits, spaces, hyphens, apostrophes, periods, and ampersands.
    Must include at least two letters.
    """
    text = " ".join((value or "").split())
    if not text:
        raise ValueError(f"{field} cannot be empty")

    if _letter_count(text) < 2:
        raise ValueError(
            f"{field} must contain at least 2 letters "
            "(cannot be only numbers or special characters)"
        )

    if not _UNIVERSITY_ALLOWED_RE.fullmatch(text):
        raise ValueError(
            f"{field} may only contain letters, numbers, spaces, hyphens (-), "
            "apostrophes ('), periods (.), and ampersands (&)"
        )
    return text
