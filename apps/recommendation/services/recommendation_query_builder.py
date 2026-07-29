from __future__ import annotations

from typing import Any, Iterable

MAX_QUERY_TOPICS = 5


def _is_non_empty_string(value: Any) -> bool:
    if value is None:
        return False
    if not isinstance(value, str):
        return False
    return bool(value.strip())


def _normalize_for_dedup(value: str) -> str:
    # case-insensitive comparison; keep first occurrence in original casing.
    return value.strip().casefold()


def _sorted_keyword_topics(keyword_scores: dict[str, Any]) -> list[str]:
    """
    Return keyword keys sorted by score descending (stable for ties).
    """
    items = list(keyword_scores.items())

    def _score(item: tuple[str, Any]) -> int:
        _key, raw_score = item
        try:
            return int(raw_score)
        except (TypeError, ValueError):
            return 0

    # Stable sort by (-score, original_index).
    indexed = list(enumerate(items))
    indexed.sort(key=lambda t: (-_score(t[1]), t[0]))
    return [key for (_idx, (key, _raw_score)) in indexed if _is_non_empty_string(key)]


def _remove_duplicates_case_insensitive_preserve_order(topics: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for topic in topics:
        norm = _normalize_for_dedup(topic)
        if not norm:
            continue
        if norm in seen:
            continue
        seen.add(norm)
        result.append(topic)
    return result


def _add_until_max(
    topics: list[str],
    *,
    candidate_values: Iterable[Any],
    seen_norm: set[str],
) -> None:
    """
    Append each candidate value (string only) until MAX_QUERY_TOPICS unique topics
    are collected. Dedup is case-insensitive using seen_norm.
    """
    for candidate in candidate_values:
        if len(topics) >= MAX_QUERY_TOPICS:
            return
        if not _is_non_empty_string(candidate):
            continue
        norm = _normalize_for_dedup(candidate)
        if not norm or norm in seen_norm:
            continue
        seen_norm.add(norm)
        topics.append(candidate)


def build_boolean_query(topics: list[str]) -> str:
    """
    Build the Semantic Scholar Boolean query from topics.

    Example:
        ["AI", "ML"] -> ("AI"|"ML")
    """
    if not topics:
        return ""
    quoted = [f"\"{topic}\"" for topic in topics]
    return f"({'|'.join(quoted)})"


def collect_topics(extracted_keywords: dict[str, Any]) -> list[str]:
    """
    Collect up to MAX_QUERY_TOPICS topics in priority order.

    Dedup is case-insensitive while preserving insertion order.
    Stops immediately once MAX_QUERY_TOPICS unique topics are collected.
    """
    topics: list[str] = []
    seen_norm: set[str] = set()

    # 1-3. Major + Minor + Interests (in order)
    _add_until_max(
        topics,
        candidate_values=extracted_keywords.get("major") or [],
        seen_norm=seen_norm,
    )
    if len(topics) < MAX_QUERY_TOPICS:
        _add_until_max(
            topics,
            candidate_values=extracted_keywords.get("minor") or [],
            seen_norm=seen_norm,
        )
    if len(topics) < MAX_QUERY_TOPICS:
        _add_until_max(
            topics,
            candidate_values=extracted_keywords.get("interests") or [],
            seen_norm=seen_norm,
        )

    # 4-6. Keyword dictionaries sorted by score desc
    if len(topics) < MAX_QUERY_TOPICS:
        engagement = extracted_keywords.get("engagement_keywords") or {}
        if isinstance(engagement, dict):
            _add_until_max(
                topics,
                candidate_values=_sorted_keyword_topics(engagement),
                seen_norm=seen_norm,
            )

    if len(topics) < MAX_QUERY_TOPICS:
        content_keywords = extracted_keywords.get("content_keywords") or {}
        if isinstance(content_keywords, dict):
            _add_until_max(
                topics,
                candidate_values=_sorted_keyword_topics(content_keywords),
                seen_norm=seen_norm,
            )

    if len(topics) < MAX_QUERY_TOPICS:
        hashtags = extracted_keywords.get("hashtags") or {}
        if isinstance(hashtags, dict):
            _add_until_max(
                topics,
                candidate_values=_sorted_keyword_topics(hashtags),
                seen_norm=seen_norm,
            )

    # Final de-dupe while preserving insertion order.
    return _remove_duplicates_case_insensitive_preserve_order(topics)[:MAX_QUERY_TOPICS]


def remove_duplicates(topics: list[str]) -> list[str]:
    """Public wrapper for dedup logic (case-insensitive, preserves first occurrence)."""
    return _remove_duplicates_case_insensitive_preserve_order(topics)


def sort_keyword_dict(keyword_scores: dict[str, Any]) -> list[str]:
    """Public wrapper that returns keys sorted by score desc."""
    return _sorted_keyword_topics(keyword_scores)


def build_semantic_scholar_query(extracted_keywords: dict[str, Any]) -> str:
    """
    Build the Semantic Scholar Boolean query from `profiles.extracted_keywords`.

    Note: This function does *not* URL encode the query. `httpx` will encode it
    when used as a query parameter.
    """
    topics = collect_topics(extracted_keywords or {})
    return build_boolean_query(topics)

