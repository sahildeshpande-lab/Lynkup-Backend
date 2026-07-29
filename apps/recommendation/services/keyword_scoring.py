"""
Keyword scoring utilities for the recommendation engine.

Scores accumulate over time so frequently occurring keywords gain higher
importance. The helpers are generic and can be used for content keywords,
hashtags, and engagement keywords.

Example usage::

    from apps.recommendation.services.keyword_scoring import (
        get_top_keywords,
        update_keyword_scores,
    )

    content_keywords: dict[str, int] = {"rag": 7, "vector database": 2}

    extracted = ["RAG", "semantic search", "semantic search"]
    content_keywords = update_keyword_scores(content_keywords, extracted)

    top_terms = get_top_keywords(content_keywords, top_n=5)
    # ["rag", "semantic search", "vector database"]
"""

from __future__ import annotations

from typing import Any


def _normalize_keyword(keyword: str) -> str:
    return keyword.strip().lower()


def update_keyword_scores(
    existing: dict[str, int],
    new_keywords: list[str],
    increment: int = 1,
) -> dict[str, int]:
    """
    Merge new keywords into an existing score map.

    Each keyword is lowercased and trimmed. Empty strings are ignored.
    Duplicate entries in ``new_keywords`` only increment once per call.
    Existing keywords have their score increased by ``increment``; new
    keywords start at ``increment``.

    Args:
        existing: Current keyword-to-score mapping.
        new_keywords: Keywords detected in the latest extraction pass.
        increment: Score delta applied to each new or repeated keyword.

    Returns:
        Updated keyword score dictionary (a shallow copy of ``existing``).
    """
    updated = dict(existing)
    seen_in_batch: set[str] = set()

    for keyword in new_keywords:
        normalized = _normalize_keyword(keyword)
        if not normalized or normalized in seen_in_batch:
            continue
        seen_in_batch.add(normalized)
        updated[normalized] = updated.get(normalized, 0) + increment

    return updated


def subtract_keyword_scores(
    existing: dict[str, int],
    removed: dict[str, int],
) -> dict[str, int]:
    """
    Remove keyword score contributions from an aggregate map.

    Scores are reduced by the values in ``removed``. Keywords whose score
    reaches zero or below are dropped from the result.
    """
    updated = dict(existing)

    for keyword, score in removed.items():
        normalized = _normalize_keyword(str(keyword))
        if not normalized:
            continue
        try:
            delta = int(score)
        except (TypeError, ValueError):
            delta = 1
        if delta <= 0:
            continue

        remaining = updated.get(normalized, 0) - delta
        if remaining <= 0:
            updated.pop(normalized, None)
        else:
            updated[normalized] = remaining

    return updated


def get_top_keywords(keyword_scores: dict[str, int], top_n: int = 5) -> list[str]:
    """
    Return the highest-scoring keywords without their scores.

    Keywords are sorted by score descending. Ties are broken alphabetically
    for deterministic ordering.

    Args:
        keyword_scores: Keyword-to-score mapping.
        top_n: Maximum number of keyword names to return.

    Returns:
        Keyword names ordered by importance.
    """
    if top_n <= 0:
        return []

    ranked = sorted(keyword_scores.items(), key=lambda item: (-item[1], item[0]))
    return [keyword for keyword, _ in ranked[:top_n]]


def coerce_keyword_scores(value: Any) -> dict[str, int]:
    """
    Normalize legacy list or dict keyword storage into a score map.

    Lists are converted with each entry starting at score 1. Dict values are
    cast to integers; invalid values default to 1.
    """
    if isinstance(value, dict):
        scores: dict[str, int] = {}
        for raw_key, raw_score in value.items():
            key = _normalize_keyword(str(raw_key))
            if not key:
                continue
            try:
                score = int(raw_score)
            except (TypeError, ValueError):
                score = 1
            scores[key] = scores.get(key, 0) + max(score, 0)
        return scores

    if isinstance(value, list):
        return update_keyword_scores({}, [str(item) for item in value], increment=1)

    return {}
