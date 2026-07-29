"""
Post-processing pipeline for KeyBERT keyword candidates.

Applied after KeyBERT extraction and before keyword score persistence.
Hashtag extraction, spaCy preprocessing, and KeyBERT calls remain in algorithm.py.
"""

from __future__ import annotations

from typing import Iterable

from apps.recommendation.config import settings

# POS tags that qualify a keyword when present.
_ALLOWED_POS = frozenset({"NOUN", "PROPN", "ADJ"})
# Keywords made only of these tags are rejected.
_REJECT_ONLY_POS = frozenset({"VERB", "AUX", "PRON", "DET"})

DEFAULT_KEYWORD_BLACKLIST = frozenset(
    {
        "good",
        "great",
        "nice",
        "today",
        "know",
        "come",
        "see",
        "hello",
        "hi",
        "connection",
        "tutorial",
    }
)


def _nlp_model():
    from apps.recommendation.services.algorithm import nlp

    return nlp


def filter_by_score(
    keywords: list[tuple[str, float]],
    *,
    min_score: float | None = None,
) -> list[tuple[str, float]]:
    """Discard keywords below the minimum KeyBERT score threshold."""
    threshold = settings.min_keyword_score if min_score is None else min_score
    return [(keyword, score) for keyword, score in keywords if score >= threshold]


def normalize_keyword(keyword: str) -> str:
    """
    Normalize a keyword for deduplication and storage.

    Lowercases, trims, collapses whitespace, and lemmatizes each token with spaCy.
    """
    collapsed = " ".join((keyword or "").strip().lower().split())
    if not collapsed:
        return ""

    doc = _nlp_model()(collapsed)
    lemmas = [token.lemma_.lower() for token in doc if not token.is_space and token.lemma_.strip()]
    return " ".join(lemmas).strip()


def _dedupe_after_normalization(
    keywords: list[tuple[str, float]],
) -> list[tuple[str, float]]:
    """Keep the highest KeyBERT score when normalization collapses duplicates."""
    best_scores: dict[str, float] = {}
    for keyword, score in keywords:
        normalized = normalize_keyword(keyword)
        if not normalized:
            continue
        existing = best_scores.get(normalized)
        if existing is None or score > existing:
            best_scores[normalized] = score
    return list(best_scores.items())


def remove_blacklisted_keywords(
    keywords: list[tuple[str, float]],
    *,
    blacklist: Iterable[str] | None = None,
) -> list[tuple[str, float]]:
    """Remove exact matches against the normalized blacklist."""
    blocked = (
        {item.strip().lower() for item in blacklist if item and str(item).strip()}
        if blacklist is not None
        else set(DEFAULT_KEYWORD_BLACKLIST)
    )
    return [
        (keyword, score)
        for keyword, score in keywords
        if keyword.strip().lower() not in blocked
    ]


def filter_by_pos(keywords: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """
    Reject keywords whose tokens are only VERB, AUX, PRON, or DET.

    Keeps keywords that contain at least one NOUN, PROPN, or ADJ token.
    """
    nlp = _nlp_model()
    kept: list[tuple[str, float]] = []
    for keyword, score in keywords:
        doc = nlp(keyword)
        pos_tags = {token.pos_ for token in doc if not token.is_space}
        if not pos_tags:
            continue
        if pos_tags.issubset(_REJECT_ONLY_POS):
            continue
        if pos_tags & _ALLOWED_POS:
            kept.append((keyword, score))
    return kept


def _is_subphrase(shorter: str, longer: str) -> bool:
    short_tokens = shorter.split()
    long_tokens = longer.split()
    if len(short_tokens) >= len(long_tokens):
        return False
    for index in range(len(long_tokens) - len(short_tokens) + 1):
        if long_tokens[index : index + len(short_tokens)] == short_tokens:
            return True
    return False


def remove_subphrase_duplicates(
    keywords: list[tuple[str, float]],
) -> list[tuple[str, float]]:
    """Prefer the longest phrase when one keyword is a subphrase of another."""
    if not keywords:
        return []

    by_length = sorted(
        keywords,
        key=lambda item: (len(item[0].split()), len(item[0]), item[1]),
        reverse=True,
    )
    kept: list[tuple[str, float]] = []
    for keyword, score in by_length:
        if any(_is_subphrase(keyword, other) for other, _ in kept):
            continue
        kept.append((keyword, score))
    return kept


def sort_keywords(keywords: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Sort keywords by KeyBERT score descending, then alphabetically."""
    return sorted(keywords, key=lambda item: (-item[1], item[0]))


def clean_keywords(
    keywords: list[tuple[str, float]],
    *,
    min_score: float | None = None,
    blacklist: Iterable[str] | None = None,
) -> list[tuple[str, float]]:
    """
    Run the full post-processing pipeline on raw KeyBERT output.

    Returns cleaned (keyword, score) tuples sorted by score descending.
    """
    if not keywords:
        return []

    filtered = filter_by_score(keywords, min_score=min_score)
    deduped = _dedupe_after_normalization(filtered)
    deduped = remove_blacklisted_keywords(deduped, blacklist=blacklist)
    deduped = filter_by_pos(deduped)
    deduped = remove_subphrase_duplicates(deduped)
    return sort_keywords(deduped)
