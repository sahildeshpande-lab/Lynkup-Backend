from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from common.exceptions import ApiError


# Previous engagement-score cursor (kept for rollback):
# def encode_cursor(*, engagement_score: int, created_at: datetime, post_id: UUID) -> str:
#     """Encode feed keyset pagination state as an opaque Base64 cursor."""
#     payload = {
#         "engagement_score": engagement_score,
#         "created_at": created_at.isoformat(),
#         "id": str(post_id),
#     }
#     raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
#     return base64.urlsafe_b64encode(raw).decode("ascii")


def encode_cursor(*, created_at: datetime, post_id: UUID) -> str:
    """Encode feed keyset pagination state as an opaque Base64 cursor."""
    payload = {
        "created_at": created_at.isoformat(),
        "id": str(post_id),
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(cursor: str) -> dict[str, Any]:
    """Decode an opaque feed cursor into keyset filter values.

    Returns:
        dict with keys: created_at (datetime), id (UUID)

    Older cursors that also carried ``relevance`` or ``engagement_score`` are
    still accepted; those fields are ignored because the feed now orders by
    created_at (reinstate uses updated_at) only.
    """
    if not cursor or not isinstance(cursor, str):
        raise ApiError("Invalid cursor")

    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
        # engagement_score = int(payload["engagement_score"])
        created_at = datetime.fromisoformat(payload["created_at"])
        post_id = UUID(str(payload["id"]))
    except (ApiError, KeyError, TypeError, ValueError, json.JSONDecodeError, OSError) as exc:
        raise ApiError("Invalid cursor") from exc

    return {
        # "engagement_score" : engagement_score,
        "created_at": created_at,
        "id": post_id,
    }
