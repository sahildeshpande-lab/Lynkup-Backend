"""Opaque keyset cursor for GET /api/v1/posts timeline pagination."""

from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from common.exceptions import ApiError

_NULL_REPOST_SENTINEL = "00000000-0000-0000-0000-000000000000"


def encode_user_posts_cursor(
    *,
    sort_at: datetime,
    post_id: UUID,
    repost_id: UUID | None,
) -> str:
    """Encode timeline keyset state (sort_at, post_id, repost_id) as opaque Base64."""
    payload = {
        "sort_at": sort_at.isoformat(),
        "post_id": str(post_id),
        "repost_id": str(repost_id) if repost_id is not None else None,
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_user_posts_cursor(cursor: str) -> dict[str, Any]:
    """Decode an opaque user-posts cursor into keyset filter values."""
    if not cursor or not isinstance(cursor, str):
        raise ApiError("Invalid cursor")

    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
        sort_at = datetime.fromisoformat(payload["sort_at"])
        post_id = UUID(str(payload["post_id"]))
        repost_raw = payload.get("repost_id")
        repost_id = UUID(str(repost_raw)) if repost_raw else None
    except (ApiError, KeyError, TypeError, ValueError, json.JSONDecodeError, OSError) as exc:
        raise ApiError("Invalid cursor") from exc

    return {
        "sort_at": sort_at,
        "post_id": post_id,
        "repost_id": repost_id,
    }


def repost_id_for_keyset(repost_id: UUID | None) -> UUID:
    """Map NULL repost_id (authored events) to a stable sentinel for comparisons."""
    return repost_id if repost_id is not None else UUID(_NULL_REPOST_SENTINEL)
