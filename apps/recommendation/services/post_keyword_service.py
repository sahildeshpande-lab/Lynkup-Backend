from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from apps.feed.content_utils import extract_hashtags, normalize_text
from apps.profiles.db_models.academic_interests_db_model import AcademicInterest
from apps.profiles.db_models.profile_db_model import Profile
from apps.recommendation.services.keyword_scoring import (
    coerce_keyword_scores,
    update_keyword_scores,
)

logger = logging.getLogger(__name__)

# Project-root debug store for post keyword extractions (temporary).
EXTRACTION_JSON_PATH = Path(__file__).resolve().parents[3] / "extraction.json"

_EXTRACTION_LIST_FIELDS = ("major", "minor", "interests")
_EXTRACTION_SCORE_FIELDS = ("hashtags", "engagement_keywords", "content_keywords")


def build_post_plain_text(content: dict | None) -> str:
    """Combine caption and HTML body into plain text for keyword extraction."""
    if not content:
        return ""

    parts: list[str] = []
    caption = content.get("caption")
    if caption:
        parts.append(str(caption))

    content_html = content.get("content_html")
    if content_html:
        parts.append(normalize_text(str(content_html)))

    return " ".join(parts).strip()


def _extract_keywords_sync(text: str) -> dict:
    from apps.recommendation.services.algorithm import extract_post_keywords

    return extract_post_keywords(text)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _non_empty_list(value: str | None) -> list[str]:
    cleaned = (value or "").strip()
    return [cleaned] if cleaned else []


def _read_extraction_raw(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to read extraction file path=%s", path)
        return None


def _find_user_record_in_raw(raw: Any, user_id: UUID) -> dict[str, Any] | None:
    user_id_str = str(user_id)

    if isinstance(raw, dict):
        if raw.get("user_id") == user_id_str:
            return raw
        users = raw.get("users")
        if isinstance(users, dict):
            entry = users.get(user_id_str)
            if isinstance(entry, dict):
                return entry
        return None

    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict) and entry.get("user_id") == user_id_str:
                return entry
        for entry in raw:
            if isinstance(entry, dict) and entry.get("post_id") and entry.get("user_id") == user_id_str:
                return entry

    return None


def _existing_keyword_scores(path: Path, user_id: UUID, field: str) -> dict[str, int]:
    record = _find_user_record_in_raw(_read_extraction_raw(path), user_id)
    if not record:
        return {}
    return coerce_keyword_scores(record.get(field))


def _post_hashtags(content: dict | None) -> list[str]:
    if not content:
        return []
    return extract_hashtags(
        caption=content.get("caption"),
        content_html=content.get("content_html"),
    )


async def _profile_interest_names(db: AsyncSession, profile: Profile | None) -> list[str]:
    if profile is None or not profile.profile_interests_id:
        return []

    interest_ids: list[int] = []
    for raw_id in profile.profile_interests_id:
        if raw_id is None:
            continue
        try:
            interest_ids.append(int(raw_id))
        except (TypeError, ValueError):
            continue

    if not interest_ids:
        return []

    rows = (
        await db.execute(
            select(AcademicInterest.name).where(
                AcademicInterest.id.in_(interest_ids),
                AcademicInterest.is_active.is_(True),
            )
        )
    ).scalars().all()
    return [(name or "").strip() for name in rows if (name or "").strip()]


async def build_post_recommendation_payload(
    db: AsyncSession,
    *,
    user_id: UUID,
    content: dict | None,
    extraction_path: Path | None = None,
) -> dict[str, Any]:
    profile = (
        await db.execute(select(Profile).where(Profile.user_id == user_id))
    ).scalar_one_or_none()

    path = extraction_path or EXTRACTION_JSON_PATH
    existing_record = _find_user_record_in_raw(_read_extraction_raw(path), user_id)

    existing_content_keywords = coerce_keyword_scores(
        existing_record.get("content_keywords") if existing_record else None
    )
    existing_hashtags = coerce_keyword_scores(
        existing_record.get("hashtags") if existing_record else None
    )
    existing_engagement_keywords = coerce_keyword_scores(
        existing_record.get("engagement_keywords") if existing_record else None
    )

    text = build_post_plain_text(content)
    new_keywords: list[str] = []
    if text:
        result = await asyncio.to_thread(_extract_keywords_sync, text)
        new_keywords = list(result.get("keywords") or [])

    return {
        "major": _non_empty_list(profile.major if profile else None),
        "minor": _non_empty_list(profile.minor if profile else None),
        "interests": await _profile_interest_names(db, profile),
        "hashtags": update_keyword_scores(existing_hashtags, _post_hashtags(content)),
        "engagement_keywords": existing_engagement_keywords,
        "content_keywords": update_keyword_scores(existing_content_keywords, new_keywords),
    }


def _persist_extraction_sync(
    path: Path,
    *,
    post_id: UUID,
    user_id: UUID,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Replace extraction.json with one cumulative record for the current user."""
    user_id_str = str(user_id)
    post_id_str = str(post_id)
    now = _utc_now_iso()

    existing = _find_user_record_in_raw(_read_extraction_raw(path), user_id)

    post_ids: list[str] = []
    if existing and existing.get("user_id") == user_id_str:
        post_ids = [str(value) for value in (existing.get("post_ids") or []) if value]
        legacy_post_id = existing.get("post_id") or existing.get("latest_post_id")
        if legacy_post_id and str(legacy_post_id) not in post_ids:
            post_ids.append(str(legacy_post_id))
    if post_id_str not in post_ids:
        post_ids.append(post_id_str)

    record: dict[str, Any] = {
        "user_id": user_id_str,
        "latest_post_id": post_id_str,
        "post_ids": post_ids,
        "updated_at": now,
    }
    for field in _EXTRACTION_LIST_FIELDS:
        record[field] = list(payload.get(field) or [])
    for field in _EXTRACTION_SCORE_FIELDS:
        record[field] = dict(payload.get(field) or {})

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


async def persist_post_extraction(
    post_id: UUID,
    *,
    user_id: UUID,
    payload: dict[str, Any],
    path: Path | None = None,
) -> dict[str, Any]:
    target = path or EXTRACTION_JSON_PATH
    return await asyncio.to_thread(
        _persist_extraction_sync,
        target,
        post_id=post_id,
        user_id=user_id,
        payload=payload,
    )


async def log_post_keywords(
    post_id: UUID,
    content: dict | None,
    *,
    user_id: UUID,
    db: AsyncSession,
) -> None:
    """
    Build the recommendation payload, print it, and store it in extraction.json.
    """
    payload = await build_post_recommendation_payload(
        db,
        user_id=user_id,
        content=content,
    )
    record = await persist_post_extraction(post_id, user_id=user_id, payload=payload)
    print(f"[post-keywords] post_id={post_id}")
    print(json.dumps(record, indent=2))


async def log_post_keywords_best_effort(
    post_id: UUID,
    content: dict | None,
    *,
    user_id: UUID,
    db: AsyncSession,
) -> None:
    """Never raise — keyword logging must not block post create/edit."""
    try:
        await log_post_keywords(post_id, content, user_id=user_id, db=db)
    except Exception:
        logger.exception("Post keyword extraction failed post_id=%s", post_id)
