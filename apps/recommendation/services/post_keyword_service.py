from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from apps.feed.content_utils import extract_hashtags, normalize_text
from apps.feed.db_models.post_db_model import Post
from apps.profiles.db_models.academic_interests_db_model import AcademicInterest
from apps.profiles.db_models.profile_db_model import Profile
from apps.recommendation.services.keyword_scoring import (
    coerce_keyword_scores,
    subtract_keyword_scores,
    update_keyword_scores,
)

logger = logging.getLogger(__name__)

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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _non_empty_list(value: str | None) -> list[str]:
    cleaned = (value or "").strip()
    return [cleaned] if cleaned else []


def _profile_keyword_record(profile: Profile | None) -> dict[str, Any]:
    if profile is None or not profile.extracted_keywords:
        return {}
    if isinstance(profile.extracted_keywords, dict):
        return profile.extracted_keywords
    return {}


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
    previous_post_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile = (
        await db.execute(select(Profile).where(Profile.user_id == user_id))
    ).scalar_one_or_none()

    existing_record = _profile_keyword_record(profile)

    existing_content_keywords = coerce_keyword_scores(
        existing_record.get("content_keywords")
    )
    existing_hashtags = coerce_keyword_scores(existing_record.get("hashtags"))
    existing_engagement_keywords = coerce_keyword_scores(
        existing_record.get("engagement_keywords")
    )

    if previous_post_snapshot:
        existing_content_keywords = subtract_keyword_scores(
            existing_content_keywords,
            coerce_keyword_scores(previous_post_snapshot.get("content_keywords")),
        )
        existing_hashtags = subtract_keyword_scores(
            existing_hashtags,
            coerce_keyword_scores(previous_post_snapshot.get("hashtags")),
        )

    text = build_post_plain_text(content)
    new_keywords: list[str] = []
    if text:
        result = await asyncio.to_thread(_extract_keywords_sync, text)
        new_keywords = list(result.get("keywords") or [])

    post_snapshot = {
        "content_keywords": update_keyword_scores({}, new_keywords),
        "hashtags": update_keyword_scores({}, _post_hashtags(content)),
    }

    return {
        "major": _non_empty_list(profile.major if profile else None),
        "minor": _non_empty_list(profile.minor if profile else None),
        "interests": await _profile_interest_names(db, profile),
        "hashtags": update_keyword_scores(existing_hashtags, _post_hashtags(content)),
        "engagement_keywords": existing_engagement_keywords,
        "content_keywords": update_keyword_scores(existing_content_keywords, new_keywords),
        "_post_snapshot": post_snapshot,
    }


def _build_profile_extracted_keywords(
    existing: dict[str, Any] | None,
    *,
    post_id: UUID,
    payload: dict[str, Any],
) -> dict[str, Any]:
    existing = existing or {}
    post_id_str = str(post_id)

    post_ids: list[str] = [str(value) for value in (existing.get("post_ids") or []) if value]
    legacy_post_id = existing.get("latest_post_id")
    if legacy_post_id and str(legacy_post_id) not in post_ids:
        post_ids.append(str(legacy_post_id))
    if post_id_str not in post_ids:
        post_ids.append(post_id_str)

    record: dict[str, Any] = {
        "latest_post_id": post_id_str,
        "post_ids": post_ids,
    }
    for field in _EXTRACTION_LIST_FIELDS:
        record[field] = list(payload.get(field) or [])
    for field in _EXTRACTION_SCORE_FIELDS:
        record[field] = dict(payload.get(field) or {})
    return record


async def _build_profile_fields_snapshot(
    db: AsyncSession,
    profile: Profile,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    existing = existing or {}
    record: dict[str, Any] = {
        "major": _non_empty_list(profile.major),
        "minor": _non_empty_list(profile.minor),
        "interests": await _profile_interest_names(db, profile),
    }

    if existing.get("latest_post_id"):
        record["latest_post_id"] = existing.get("latest_post_id")
    post_ids = existing.get("post_ids")
    if post_ids:
        record["post_ids"] = list(post_ids)

    for field in _EXTRACTION_SCORE_FIELDS:
        record[field] = dict(existing.get(field) or {})

    return record


async def refresh_profile_extracted_keywords(
    db: AsyncSession,
    *,
    user_id: UUID,
) -> dict[str, Any]:
    """Sync profile major/minor/interests into extracted_keywords without touching post scores."""
    profile = (
        await db.execute(select(Profile).where(Profile.user_id == user_id))
    ).scalar_one_or_none()
    if profile is None:
        logger.warning(
            "Skipping profile keyword refresh; profile not found user_id=%s",
            user_id,
        )
        return {}

    record = await _build_profile_fields_snapshot(
        db,
        profile,
        _profile_keyword_record(profile),
    )
    profile.extracted_keywords = record
    profile.keywords_updated_at = _utc_now()
    db.add(profile)
    await db.commit()
    return record


async def refresh_profile_extracted_keywords_best_effort(
    db: AsyncSession,
    *,
    user_id: UUID,
) -> None:
    """Never raise — keyword refresh must not block profile updates."""
    try:
        await refresh_profile_extracted_keywords(db, user_id=user_id)
    except Exception:
        logger.exception("Profile keyword refresh failed user_id=%s", user_id)


async def persist_post_keyword_snapshot(
    db: AsyncSession,
    post_id: UUID,
    snapshot: dict[str, Any],
) -> None:
    """Store per-post extracted keywords for engagement reuse."""
    post = (
        await db.execute(select(Post).where(Post.id == post_id))
    ).scalar_one_or_none()
    if post is None:
        logger.warning("Skipping post keyword snapshot; post not found post_id=%s", post_id)
        return

    post.extracted_keywords = {
        "content_keywords": dict(snapshot.get("content_keywords") or {}),
        "hashtags": dict(snapshot.get("hashtags") or {}),
    }
    post.keywords_updated_at = _utc_now()
    db.add(post)
    await db.commit()


async def persist_profile_extracted_keywords(
    db: AsyncSession,
    *,
    post_id: UUID,
    user_id: UUID,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Persist cumulative keyword profile data on the user's profile row."""
    profile = (
        await db.execute(select(Profile).where(Profile.user_id == user_id))
    ).scalar_one_or_none()
    if profile is None:
        logger.warning(
            "Skipping profile keyword persistence; profile not found user_id=%s",
            user_id,
        )
        return {}

    record = _build_profile_extracted_keywords(
        _profile_keyword_record(profile),
        post_id=post_id,
        payload=payload,
    )
    profile.extracted_keywords = record
    profile.keywords_updated_at = _utc_now()
    db.add(profile)
    await db.commit()
    return record


async def log_post_keywords(
    post_id: UUID,
    content: dict | None,
    *,
    user_id: UUID,
    db: AsyncSession,
) -> None:
    """Build the recommendation payload, print it, and store it on profile/post rows."""
    post = (
        await db.execute(select(Post).where(Post.id == post_id))
    ).scalar_one_or_none()
    previous_post_snapshot = None
    if post is not None and isinstance(post.extracted_keywords, dict):
        previous_post_snapshot = post.extracted_keywords

    payload = await build_post_recommendation_payload(
        db,
        user_id=user_id,
        content=content,
        previous_post_snapshot=previous_post_snapshot,
    )
    post_snapshot = payload.pop("_post_snapshot", {})
    await persist_post_keyword_snapshot(db, post_id, post_snapshot)
    record = await persist_profile_extracted_keywords(
        db,
        post_id=post_id,
        user_id=user_id,
        payload=payload,
    )
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
