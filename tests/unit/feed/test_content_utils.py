from __future__ import annotations

import pytest

from apps.feed.content_utils import extract_hashtags, validate_hashtag_count, validate_content
from apps.feed.schemas import PostContentPayload


def test_extract_hashtags_from_caption_and_content_html() -> None:
    tags = extract_hashtags(
        caption="Hello #World",
        content_html="<p>Learning #FastAPI and #PostgreSQL</p>",
    )
    assert tags == ["world", "fastapi", "postgresql"]


def test_extract_hashtags_deduplicates_across_fields() -> None:
    tags = extract_hashtags(
        caption="#Python rocks",
        content_html="<p>More #Python here</p>",
    )
    assert tags == ["python"]


def test_validate_hashtag_count_rejects_over_limit() -> None:
    caption = " ".join(f"#{i}" for i in range(6))
    with pytest.raises(ValueError, match="Maximum 5 hashtags"):
        validate_hashtag_count(caption, None, limit=5)


def test_validate_content_enforces_hashtag_limit() -> None:
    content = {
        "caption": " ".join(f"#{i}" for i in range(6)),
        "content_html": "<p>Post body</p>",
        "visibility": "public",
    }
    with pytest.raises(ValueError, match="Maximum 5 hashtags"):
        validate_content(content)


def test_validate_content_allows_caption_only() -> None:
    validate_content({"caption": "Caption only", "visibility": "public"})


def test_validate_content_allows_missing_caption() -> None:
    validate_content(
        {
            "content_html": "<p>Post body without a caption</p>",
            "visibility": "public",
        }
    )


def test_post_content_payload_defaults_caption_to_none() -> None:
    payload = PostContentPayload(content_html="<p>Media description</p>")

    assert payload.caption is None
