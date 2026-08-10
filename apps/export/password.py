"""Deterministic 6-character ZIP password generation for data exports.

The password is derived from the user profile fields and is:
  - exactly 6 characters (2-char initials + 4-char hex suffix)
  - uppercase letters and digits only  [A-Z0-9]
  - deterministic: same inputs produce the same password
  - NOT stored in the database
  - NOT logged anywhere
  - NOT returned in any API response

Format::

    <initial_first><initial_last><hex4>

    Where <hex4> is the first 4 uppercase hex chars of the SHA-256 hash
    of the pipe-separated, normalised concatenation of all five profile fields.

Example::

    first_name='Sahil', last_name='Deshpande',
    university='APCOE', major='AIDS', minor='Data Science'
    -> 'SD' + SHA256('sahil|deshpande|apcoe|aids|data science').upper()[:4]
    -> e.g. 'SD7F2C'
"""
from __future__ import annotations

import hashlib
import re


def generate_export_password(
    *,
    first_name: str | None,
    last_name: str | None,
    university: str | None,
    major: str | None,
    minor: str | None,
) -> str:
    """Return a deterministic 6-character export ZIP password.

    All inputs are optional; None values are treated as empty strings.
    The returned password is always exactly 6 uppercase alphanumeric characters.

    Security note: the returned value must be passed only to ZIP encryption
    and the export email.  Do NOT log it, store it, or expose it in API
    responses.
    """
    fn = _normalize(first_name)
    ln = _normalize(last_name)
    uni = _normalize(university)
    maj = _normalize(major)
    mn = _normalize(minor)

    initial_f = fn[0].upper() if fn else "X"
    initial_l = ln[0].upper() if ln else "X"

    combined = f"{fn}|{ln}|{uni}|{maj}|{mn}"
    digest = hashlib.sha256(combined.encode("utf-8")).hexdigest().upper()

    suffix = digest[:4]
    password = initial_f + initial_l + suffix  # exactly 6 chars
    return password


def _normalize(value: str | None) -> str:
    """Lowercase, strip whitespace, collapse internal whitespace."""
    if not value:
        return ""
    text = value.strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text
