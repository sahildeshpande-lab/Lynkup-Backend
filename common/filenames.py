"""Shared helpers for uploaded filenames."""

from __future__ import annotations

from urllib.parse import unquote


def normalize_filename(filename: str | None) -> str | None:
    """Return a human-readable filename, decoding URL-encoded values once.

    Already-decoded names are left unchanged. ``None`` and empty strings are
    returned as-is. Malformed percent-encoding does not raise so upload flows
    are not interrupted.
    """
    if filename is None:
        return None
    try:
        return unquote(filename)
    except Exception:
        return filename
