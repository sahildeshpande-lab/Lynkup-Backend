"""
Content utilities for the feed module.

Provides HTML sanitization, hashtag extraction, text normalization,
and content validation for posts.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

import bleach


# ---------------------------------------------------------------------------
# Allowed HTML elements and attributes for sanitization
# ---------------------------------------------------------------------------
ALLOWED_TAGS = [
    "p", "b", "i", "u", "strong", "em", "a", "br",
    "ul", "ol", "li",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "code", "pre", "span", "img",
]

ALLOWED_ATTRIBUTES = {
    "a": ["href"],
    "img": ["src", "alt"],
    "span": ["class"],
}

# Maximum character count for normalized (plain-text) content
MAX_CONTENT_CHARS = 5000

# Maximum number of media attachments per post
MAX_MEDIA_COUNT = 5

# Hashtag extraction pattern: matches #Word (alphanumeric + underscore)
_HASHTAG_RE = re.compile(r"#([A-Za-z0-9_]+)")

# Document MIME types and extensions allowed for document attachments
ALLOWED_DOCUMENT_EXTENSIONS = {".pdf", ".doc", ".docx", ".ppt", ".pptx"}
ALLOWED_DOCUMENT_MIMETYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

# Audio MIME type prefixes allowed
ALLOWED_AUDIO_MIMETYPES = {
    "audio/mpeg",
    "audio/mp3",
    "audio/wav",
    "audio/x-wav",
    "audio/ogg",
    "audio/aac",
    "audio/mp4",
    "audio/x-m4a",
    "audio/flac",
    "audio/webm",
}

# Size limits (bytes)
MAX_DOCUMENT_SIZE = 20 * 1024 * 1024   # 20 MB
MAX_AUDIO_SIZE = 25 * 1024 * 1024      # 25 MB


# ---------------------------------------------------------------------------
# HTML text stripper
# ---------------------------------------------------------------------------
class _HTMLTextExtractor(HTMLParser):
    """Simple HTML parser that extracts only text content."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts)


def _strip_html(html: str) -> str:
    """Remove all HTML tags and return plain text."""
    extractor = _HTMLTextExtractor()
    extractor.feed(html)
    return extractor.get_text()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def sanitize_html(html: str) -> str:
    """
    Sanitize untrusted HTML, keeping only allowlisted tags and attributes.
    """
    return bleach.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        strip=True,
    )


def normalize_text(html: str) -> str:
    """
    Strip all HTML tags, collapse whitespace, and return plain text.
    Used for character counting and search indexing.
    """
    raw = _strip_html(html)
    # collapse whitespace
    return " ".join(raw.split()).strip()


def extract_hashtags(
    *,
    caption: str | None = None,
    content_html: str | None = None,
) -> list[str]:
    """
    Extract hashtags from caption and content_html combined.

    Given caption ``"Hello #World"`` and content ``"<p>#FastAPI</p>"``
    returns ``["world", "fastapi"]`` (lowercased, deduplicated, order preserved).
    """
    parts: list[str] = []
    if caption:
        parts.append(str(caption))
    if content_html:
        parts.append(_strip_html(content_html))
    plain = " ".join(parts)
    tags = _HASHTAG_RE.findall(plain)
    seen: set[str] = set()
    result: list[str] = []
    for tag in tags:
        lower = tag.lower()
        if lower not in seen:
            seen.add(lower)
            result.append(lower)
    return result


def validate_hashtag_count(
    caption: str | None,
    content_html: str | None,
    *,
    limit: int | None = None,
) -> None:
    """Raise ValueError when combined caption/content_html exceeds the hashtag limit."""
    if limit is None:
        from apps.feed.config import settings

        limit = settings.hashtag_limit

    tags = extract_hashtags(caption=caption, content_html=content_html)
    if len(tags) > limit:
        raise ValueError(
            f"Maximum {limit} hashtags allowed per post (got {len(tags)})"
        )


def validate_content(content: dict) -> None:
    """
    Validate the content payload before saving.

    Raises ``ValueError`` with a descriptive message on validation failure.

    Rules:
    - ``caption`` is mandatory.
    - ``visibility`` must be ``"public"`` or ``"private"``.
    - If ``content_html`` is provided, its normalized text must be ≤ 5 000
      Unicode characters.
    """
    caption = content.get("caption")
    if not caption or not str(caption).strip():
        raise ValueError("Caption is required")

    visibility = content.get("visibility", "public")
    if visibility not in ("public", "private"):
        raise ValueError(f"Invalid visibility value: {visibility}. Must be 'public' or 'private'")

    content_html = content.get("content_html")
    if content_html:
        normalized = normalize_text(content_html)
        if len(normalized) > MAX_CONTENT_CHARS:
            raise ValueError(
                f"Content exceeds maximum length of {MAX_CONTENT_CHARS} characters "
                f"(got {len(normalized)})"
            )

    validate_hashtag_count(content.get("caption"), content_html)


def validate_media_count(media: list | None) -> None:
    """
    Reject if more than ``MAX_MEDIA_COUNT`` media items are attached.
    """
    if media and len(media) > MAX_MEDIA_COUNT:
        raise ValueError(
            f"Maximum {MAX_MEDIA_COUNT} media items allowed per post "
            f"(got {len(media)})"
        )


def validate_media_asset(media_asset, media_type_str: str) -> None:
    """
    Validate a resolved ``MediaAsset`` against the attachment rules.

    Called during attachment association (not during upload).

    Rules:
    - Documents: must be PDF/DOC/DOCX/PPT/PPTX, ≤ 20 MB.
    - Audio: must be an allowlisted audio format, ≤ 25 MB.
    - Images: validated at upload time; no additional check here.
    """
    mime = (media_asset.mime_type or "").lower()
    size = media_asset.file_size or 0

    if media_type_str == "document":
        if mime and mime not in ALLOWED_DOCUMENT_MIMETYPES:
            raise ValueError(
                f"Unsupported document type: {mime}. "
                f"Allowed: PDF, DOC, DOCX, PPT, PPTX"
            )
        if size > MAX_DOCUMENT_SIZE:
            raise ValueError(
                f"Document exceeds maximum size of 20 MB (got {size / (1024 * 1024):.1f} MB)"
            )

    elif media_type_str == "audio":
        if mime and mime not in ALLOWED_AUDIO_MIMETYPES:
            raise ValueError(
                f"Unsupported audio format: {mime}"
            )
        if size > MAX_AUDIO_SIZE:
            raise ValueError(
                f"Audio file exceeds maximum size of 25 MB (got {size / (1024 * 1024):.1f} MB)"
            )
