from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Header

from apps.share.config import settings
from common.exceptions import ApiError


def require_share_post_token(
    x_share_token: Annotated[str | None, Header(alias="X-Share-Token")] = None,
) -> None:
    """Validate the fixed share token from ``X-Share-Token``.

    Uses the same unauthorized ``ApiError`` messages as Firebase/JWT auth so
    the global handler returns HTTP 401 with the standard ApiResponse envelope.
    Never logs or returns the configured token.
    """
    provided = (x_share_token or "").strip()
    if not provided:
        raise ApiError("Missing access token")

    expected = (settings.share_post_token or "").strip()
    if not expected:
        raise ApiError("Invalid access token")

    try:
        matched = secrets.compare_digest(provided, expected)
    except (TypeError, ValueError):
        matched = False

    if not matched:
        raise ApiError("Invalid access token")
