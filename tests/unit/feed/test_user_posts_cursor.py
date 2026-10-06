from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from apps.feed.services.user_posts_cursor import (
    decode_user_posts_cursor,
    encode_user_posts_cursor,
    repost_id_for_keyset,
)
from common.exceptions import ApiError


def test_encode_decode_user_posts_cursor_roundtrip() -> None:
    sort_at = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    post_id = uuid4()
    repost_id = uuid4()

    cursor = encode_user_posts_cursor(
        sort_at=sort_at,
        post_id=post_id,
        repost_id=repost_id,
    )
    decoded = decode_user_posts_cursor(cursor)

    assert decoded["sort_at"] == sort_at
    assert decoded["post_id"] == post_id
    assert decoded["repost_id"] == repost_id


def test_encode_decode_user_posts_cursor_authored_event() -> None:
    sort_at = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    post_id = uuid4()

    cursor = encode_user_posts_cursor(
        sort_at=sort_at,
        post_id=post_id,
        repost_id=None,
    )
    decoded = decode_user_posts_cursor(cursor)

    assert decoded["repost_id"] is None
    assert repost_id_for_keyset(None) != repost_id_for_keyset(uuid4())


def test_decode_user_posts_cursor_rejects_invalid() -> None:
    with pytest.raises(ApiError):
        decode_user_posts_cursor("not-valid")
