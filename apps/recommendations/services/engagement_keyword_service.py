"""
Engagement keyword scoring for the recommendation system.

Updates a user's ``engagement_keywords`` profile when they like, bookmark,
comment on, or repost a post. Reuses post-level keywords already extracted at
post create/edit time — no spaCy or KeyBERT runs here.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from apps.feed.db_models.post_db_model import Post
from apps.profiles.db_models.profile_db_model import Profile
from apps.recommendations.services.keyword_scoring import coerce_keyword_scores

logger = logging.getLogger(__name__)

ENGAGEMENT_WEIGHTS: dict[str, int] = {
    "like": 1,
    "bookmark": 2,
    "comment": 3,
    "repost": 4,
}


def _normalize_engagement_keyword(keyword: str) -> str:
    return " ".join((keyword or "").strip().lower().split())


def _dedupe_keyword_names(keywords: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for keyword in keywords:
        normalized = _normalize_engagement_keyword(keyword)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def update_engagement_keywords(
    existing: dict[str, int],
    keywords: list[str],
    increment: int = 1,
) -> dict[str, int]:
    """
    Increment engagement scores for unique normalized keywords.

    Duplicate entries in ``keywords`` only apply once per call.
    """
    updated = dict(existing)
    for keyword in _dedupe_keyword_names(keywords):
        updated[keyword] = updated.get(keyword, 0) + increment
    return updated


def remove_engagement_keywords(
    existing: dict[str, int],
    keywords: list[str],
    decrement: int = 1,
) -> dict[str, int]:
    """
    Decrement engagement scores and remove keys whose score becomes <= 0.
    """
    updated = dict(existing)
    for keyword in _dedupe_keyword_names(keywords):
        new_score = updated.get(keyword, 0) - decrement
        if new_score <= 0:
            updated.pop(keyword, None)
        else:
            updated[keyword] = new_score
    return updated


def merge_post_keyword_names(
    content_keywords: dict[str, int] | None,
    hashtags: dict[str, int] | None,
) -> list[str]:
    """Combine post content keyword and hashtag names (ignore stored scores)."""
    names: list[str] = []
    for mapping in (content_keywords, hashtags):
        if not mapping:
            continue
        names.extend(str(key) for key in mapping.keys())
    return _dedupe_keyword_names(names)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _load_post_keyword_snapshot(post: Post | None) -> dict[str, dict[str, int]]:
    if post is None or not post.extracted_keywords:
        return {"content_keywords": {}, "hashtags": {}}

    record = post.extracted_keywords
    if not isinstance(record, dict):
        return {"content_keywords": {}, "hashtags": {}}

    return {
        "content_keywords": coerce_keyword_scores(record.get("content_keywords")),
        "hashtags": coerce_keyword_scores(record.get("hashtags")),
    }


def _ensure_profile_keyword_defaults(record: dict[str, Any]) -> dict[str, Any]:
    from apps.recommendations.services.post_keyword_service import (
        _EXTRACTION_LIST_FIELDS,
        _EXTRACTION_SCORE_FIELDS,
    )

    updated = dict(record)
    for field in _EXTRACTION_LIST_FIELDS:
        updated.setdefault(field, [])
    for field in _EXTRACTION_SCORE_FIELDS:
        updated.setdefault(field, {})
    return updated


async def apply_engagement_keyword_update(
    db: AsyncSession,
    user_id: UUID,
    post_id: UUID,
    action: str,
    *,
    added: bool,
) -> None:
    """
    Apply or reverse an engagement keyword update for one user and post.

    Args:
        db: Active database session.
        user_id: Engaging user.
        post_id: Target post.
        action: One of ``like``, ``bookmark``, ``comment``, ``repost``.
        added: True to apply weight; False to subtract (undo).
    """
    action_key = action.strip().lower()
    weight = ENGAGEMENT_WEIGHTS.get(action_key)
    if weight is None:
        raise ValueError(f"Unsupported engagement action: {action}")

    post = (
        await db.execute(select(Post).where(Post.id == post_id))
    ).scalar_one_or_none()
    post_snapshot = _load_post_keyword_snapshot(post)
    keyword_names = merge_post_keyword_names(
        post_snapshot["content_keywords"],
        post_snapshot["hashtags"],
    )
    if not keyword_names:
        logger.info(
            "Skipping engagement keyword update; no post keywords post_id=%s",
            post_id,
        )
        return

    profile = (
        await db.execute(select(Profile).where(Profile.user_id == user_id))
    ).scalar_one_or_none()
    if profile is None:
        logger.warning(
            "Skipping engagement keyword update; profile not found user_id=%s",
            user_id,
        )
        return

    user_record = _ensure_profile_keyword_defaults(profile.extracted_keywords or {})
    existing_engagement = coerce_keyword_scores(user_record.get("engagement_keywords"))

    if added:
        updated = update_engagement_keywords(existing_engagement, keyword_names, weight)
        increment = weight
    else:
        updated = remove_engagement_keywords(existing_engagement, keyword_names, weight)
        increment = -weight

    user_record["engagement_keywords"] = updated
    profile.extracted_keywords = user_record
    profile.keywords_updated_at = _utc_now()
    db.add(profile)
    await db.commit()

    print("[engagement-keywords]")
    print(f"user_id={user_id}")
    print(f"post_id={post_id}")
    print(f"action={action_key.upper()}")
    print(f"increment={increment}")
    print(f"keywords={json.dumps(keyword_names)}")


async def apply_engagement_keyword_update_best_effort(
    db: AsyncSession,
    user_id: UUID,
    post_id: UUID,
    action: str,
    *,
    added: bool,
) -> None:
    """Never raise — engagement keyword updates must not block engagement APIs."""
    try:
        await apply_engagement_keyword_update(
            db,
            user_id,
            post_id,
            action,
            added=added,
        )
    except Exception:
        logger.exception(
            "Engagement keyword update failed user_id=%s post_id=%s action=%s added=%s",
            user_id,
            post_id,
            action,
            added,
        )
