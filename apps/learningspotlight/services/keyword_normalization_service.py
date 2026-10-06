"""Normalize Learning Spotlight keywords against DB-derived canonical vocabulary."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from rapidfuzz import fuzz, process
from sqlalchemy.ext.asyncio import AsyncSession

from apps.learningspotlight.services.academic_vocabulary_repository import (
    load_canonical_academic_terms,
)

logger = logging.getLogger(__name__)

STRONG_MATCH_THRESHOLD = 90.0
ACCEPTABLE_MATCH_THRESHOLD = 80.0
LOW_MATCH_THRESHOLD = 70.0
MIN_SCORE_GAP = 5.0
SAFE_LOW_BAND_MIN_SCORE = 75.0
SAFE_LOW_BAND_MIN_GAP = 10.0
SHORT_KEYWORD_MAX_LEN = 3
SHORT_KEYWORD_MIN_SCORE = 95.0
MIN_LENGTH_RATIO = 0.5

# Structural query extraction limits (heuristics, not a taxonomy).
MAX_PHRASES_PER_KEYWORD = 6
MAX_TERMS_PER_STAGE = 12
MIN_LIST_UNIGRAM_LEN = 4
MIN_DERIVED_UNIGRAM_LEN = 5
MIN_BIGRAM_LONGER_TOKEN_LEN = 5
MAX_SIMPLE_PHRASE_WORDS = 6
MAX_CONCEPTS_PER_GROUP = 4

# Trailing academic-label containers (not a search stoplist). "Theatre Arts"
# should contribute "theatre", while "computer science" stays a phrase.
_CATEGORY_CONTAINER_WORDS = frozenset({"arts", "studies"})
_AND_SPLIT_RE = re.compile(r"\s+(?:and|&)\s+", re.IGNORECASE)
_LEVEL_MODIFIER_SUFFIXES = ("ed", "ive", "ory", "ary", "able")

_LIST_FIELDS = ("major", "minor", "interests")
_SCORED_DICT_FIELDS = ("engagement_keywords", "content_keywords", "hashtags")

_WHITESPACE_RE = re.compile(r"\s+")
_SLASH_SPACING_RE = re.compile(r"\s*/\s*")
_COMMA_SPACING_RE = re.compile(r"\s*,\s*")
_IN_SPLIT_RE = re.compile(r"\s+in\s+", re.IGNORECASE)
_LEADING_AND_RE = re.compile(r"^(?:and|&)\s+", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*")


@dataclass(frozen=True, slots=True)
class KeywordNormalizationResult:
    original: str
    normalized: str
    score: float | None
    matched: bool
    match_type: str


@dataclass(frozen=True, slots=True)
class PreparedSearchTerms:
    """Searchable query variants derived from raw keywords.

    RapidFuzz typo-normalization and structural phrase extraction are tracked
    separately so ranking/debug can still see the original strings.
    """

    raw_keywords: tuple[str, ...]
    cleaned_keywords: tuple[str, ...]
    stage1_terms: tuple[str, ...]
    stage2_terms: tuple[str, ...]
    stage3_terms: tuple[str, ...]

    def all_terms(self) -> tuple[str, ...]:
        return _dedupe_terms([*self.stage1_terms, *self.stage2_terms, *self.stage3_terms])


class KeywordNormalizationService:
    """Normalize raw keywords using cached DB-derived canonical academic terms."""

    def __init__(self) -> None:
        self._vocabulary: list[str] = []
        self._vocabulary_comparison: list[str] = []
        self._comparison_lookup: dict[str, str] = {}
        self._normalize_cache: dict[str, KeywordNormalizationResult] = {}
        self._loaded = False

    @property
    def vocabulary(self) -> list[str]:
        return list(self._vocabulary)

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    async def ensure_loaded(self, session: AsyncSession | None = None) -> None:
        if self._loaded:
            return
        if session is None:
            return
        try:
            terms = await load_canonical_academic_terms(session)
        except Exception:
            logger.warning(
                "[keyword-normalization] failed to load vocabulary from database; "
                "continuing with pass-through normalization",
                exc_info=True,
            )
            return
        self.refresh_vocabulary(terms)

    def refresh_vocabulary(self, terms: list[str]) -> None:
        self._vocabulary = list(terms)
        self._vocabulary_comparison = [_comparison_form(term) for term in self._vocabulary]
        self._comparison_lookup = {
            form: term
            for form, term in zip(self._vocabulary_comparison, self._vocabulary)
            if form
        }
        self._normalize_cache = {}
        self._loaded = bool(self._vocabulary)
        logger.debug(
            "[keyword-normalization] refreshed vocabulary size=%d",
            len(self._vocabulary),
        )

    async def refresh_from_db(self, session: AsyncSession) -> None:
        """Reload canonical terms from the database, replacing the in-process cache."""
        terms = await load_canonical_academic_terms(session)
        self.refresh_vocabulary(terms)

    def normalize_keyword(
        self,
        raw_keyword: str,
        *,
        fuzzy: bool = True,
    ) -> KeywordNormalizationResult:
        original = str(raw_keyword or "").strip()
        if not original:
            return KeywordNormalizationResult(
                original=raw_keyword or "",
                normalized="",
                score=None,
                matched=False,
                match_type="none",
            )

        comparison_form = _comparison_form(original)
        if not comparison_form:
            return KeywordNormalizationResult(
                original=original,
                normalized=original,
                score=None,
                matched=False,
                match_type="none",
            )

        cached = self._normalize_cache.get(f"{int(fuzzy)}:{comparison_form}")
        if cached is not None:
            return KeywordNormalizationResult(
                original=original,
                normalized=cached.normalized,
                score=cached.score,
                matched=cached.matched,
                match_type=cached.match_type,
            )

        def _finish(result: KeywordNormalizationResult) -> KeywordNormalizationResult:
            self._normalize_cache[f"{int(fuzzy)}:{comparison_form}"] = result
            if result.original == original:
                return result
            return KeywordNormalizationResult(
                original=original,
                normalized=result.normalized,
                score=result.score,
                matched=result.matched,
                match_type=result.match_type,
            )

        exact = self._comparison_lookup.get(comparison_form)
        if exact is not None:
            logger.debug(
                "[keyword-normalization] exact raw=%r normalized=%r",
                original,
                exact,
            )
            return _finish(
                KeywordNormalizationResult(
                    original=original,
                    normalized=exact,
                    score=100.0,
                    matched=True,
                    match_type="exact",
                )
            )

        if not self._vocabulary or not fuzzy:
            return _finish(
                KeywordNormalizationResult(
                    original=original,
                    normalized=original,
                    score=None,
                    matched=False,
                    match_type="none",
                )
            )

        # Typo-normalization is for short/simple keywords, not long taxonomy labels.
        if _is_structured_label(original):
            return _finish(
                KeywordNormalizationResult(
                    original=original,
                    normalized=original,
                    score=None,
                    matched=False,
                    match_type="none",
                )
            )

        if len(comparison_form) <= 2:
            return _finish(
                KeywordNormalizationResult(
                    original=original,
                    normalized=original,
                    score=None,
                    matched=False,
                    match_type="none",
                )
            )

        matches = process.extract(
            comparison_form,
            self._vocabulary_comparison,
            scorer=fuzz.WRatio,
            limit=2,
        )
        if not matches:
            return _finish(
                KeywordNormalizationResult(
                    original=original,
                    normalized=original,
                    score=None,
                    matched=False,
                    match_type="none",
                )
            )

        best_comparison, best_score, _ = matches[0]
        second_score = matches[1][1] if len(matches) > 1 else 0.0
        best_canonical = self._comparison_lookup.get(best_comparison, best_comparison)

        if not _accept_fuzzy_match(
            score=best_score,
            second_score=second_score,
            comparison_form=comparison_form,
            canonical=best_canonical,
        ):
            logger.debug(
                "[keyword-normalization] rejected fuzzy raw=%r best=%r score=%.1f second=%.1f",
                original,
                best_canonical,
                best_score,
                second_score,
            )
            return _finish(
                KeywordNormalizationResult(
                    original=original,
                    normalized=original,
                    score=best_score,
                    matched=False,
                    match_type="none",
                )
            )

        logger.debug(
            "[keyword-normalization] fuzzy raw=%r normalized=%r score=%.1f",
            original,
            best_canonical,
            best_score,
        )
        return _finish(
            KeywordNormalizationResult(
                original=original,
                normalized=best_canonical,
                score=best_score,
                matched=True,
                match_type="fuzzy",
            )
        )

    def normalize_keywords(self, raw_keywords: list[str]) -> list[KeywordNormalizationResult]:
        return [self.normalize_keyword(keyword) for keyword in raw_keywords]

    def normalize_extracted_keywords(
        self,
        extracted_keywords: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not extracted_keywords:
            return {}

        normalized: dict[str, Any] = dict(extracted_keywords)

        for field_name in _LIST_FIELDS:
            values = extracted_keywords.get(field_name)
            if not isinstance(values, list):
                continue
            normalized[field_name] = self._normalize_string_list(values)

        for field_name in _SCORED_DICT_FIELDS:
            values = extracted_keywords.get(field_name)
            if not isinstance(values, dict):
                continue
            normalized[field_name] = self._normalize_scored_dict(values)

        return normalized


    def _normalize_string_list(self, values: list[Any]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            if value is None:
                continue
            raw = str(value).strip()
            if not raw:
                continue
            normalized = self.normalize_keyword(raw).normalized
            if not normalized:
                continue
            key = normalized.casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(normalized)
        return result

    def _normalize_scored_dict(self, values: dict[str, Any]) -> dict[str, int]:
        merged: dict[str, int] = {}
        for raw_key, raw_score in values.items():
            if raw_key is None:
                continue
            raw = str(raw_key).strip()
            if not raw:
                continue
            normalized = self.normalize_keyword(raw, fuzzy=False).normalized
            if not normalized:
                continue
            try:
                score = int(raw_score)
            except (TypeError, ValueError):
                score = 0
            key = normalized.casefold()
            existing_key = next(
                (existing for existing in merged if existing.casefold() == key),
                None,
            )
            if existing_key is None:
                merged[normalized] = score
            else:
                merged[existing_key] += score
        return merged

    def prepare_search_terms(self, raw_keywords: list[str] | tuple[str, ...]) -> PreparedSearchTerms:
        """Derive staged searchable terms from raw keywords.

        Typo-normalization (RapidFuzz vs DB vocabulary) and structural phrase
        extraction are applied as separate steps. Raw inputs are not rewritten.
        """
        raw: list[str] = []
        cleaned: list[str] = []
        stage1: list[str] = []
        stage2: list[str] = []
        stage3: list[str] = []

        for value in raw_keywords or []:
            if value is None:
                continue
            original = str(value).strip()
            if not original:
                continue
            raw.append(original)
            cleaned_value = basic_cleanup(original)
            if not cleaned_value:
                continue
            cleaned.append(cleaned_value)
            extracted = self._extract_structural_candidates(cleaned_value)
            stage1.extend(extracted["stage1"])
            stage2.extend(extracted["stage2"])
            stage3.extend(extracted["stage3"])

        stage1_terms = _cap_terms(stage1, MAX_TERMS_PER_STAGE)
        stage2_terms = _cap_terms([*stage1_terms, *stage2], MAX_TERMS_PER_STAGE)
        stage3_terms = _cap_terms([*stage2_terms, *stage3], MAX_TERMS_PER_STAGE)

        logger.debug(
            "[keyword-normalization] search-term preparation raw=%r cleaned=%r "
            "stage1=%r stage2=%r stage3=%r",
            raw,
            cleaned,
            stage1_terms,
            stage2_terms,
            stage3_terms,
        )
        return PreparedSearchTerms(
            raw_keywords=tuple(raw),
            cleaned_keywords=tuple(_dedupe_terms(cleaned)),
            stage1_terms=tuple(stage1_terms),
            stage2_terms=tuple(stage2_terms),
            stage3_terms=tuple(stage3_terms),
        )

    def _extract_structural_candidates(self, cleaned: str) -> dict[str, list[str]]:
        """Split a cleaned keyword into staged query candidates.

        Stage 1: original simple phrases and strong multi-word / list segments.
        Stage 2: additional structural phrases (path leaves, extra n-grams).
        Stage 3: substantial unigrams derived from list/path structure.
        """
        stage1: list[str] = []
        stage2: list[str] = []
        stage3: list[str] = []

        if not _is_structured_label(cleaned):
            simple = self._finalize_term(cleaned)
            if simple:
                if _word_count(simple) <= MAX_SIMPLE_PHRASE_WORDS:
                    stage1.append(simple)
                else:
                    tokens = _tokenize(simple)
                    stage1.extend(
                        self._finalize_terms(_multiword_from_tokens(tokens), allow_fuzzy=False)
                    )
            return {
                "stage1": _cap_terms(stage1, MAX_PHRASES_PER_KEYWORD),
                "stage2": [],
                "stage3": _cap_terms(stage3, MAX_PHRASES_PER_KEYWORD),
            }

        head, tail = _split_head_and_tail(cleaned)
        phrase_source = ""
        path_source = ""
        if tail:
            phrase_source = head
            path_source = tail
        elif "," in cleaned and "/" not in cleaned:
            phrase_source = cleaned
        elif "/" in cleaned:
            path_source = cleaned
        else:
            phrase_source = cleaned

        if phrase_source:
            self._collect_from_phrase_or_list(phrase_source, stage1, stage2)
        if path_source:
            self._collect_from_slash_path(path_source, stage1, stage2, stage3)

        return {
            "stage1": _cap_terms(stage1, MAX_PHRASES_PER_KEYWORD),
            "stage2": _cap_terms(stage2, MAX_PHRASES_PER_KEYWORD),
            "stage3": _cap_terms(stage3, MAX_PHRASES_PER_KEYWORD),
        }

    def _collect_from_phrase_or_list(
        self,
        source: str,
        stage1: list[str],
        stage2: list[str],
    ) -> None:
        items = _comma_segments(source)
        if not items:
            items = [source]
        is_list = len(items) > 1
        for item in items:
            tokens = _tokenize(item)
            if not tokens:
                continue
            if len(tokens) == 1:
                min_len = MIN_LIST_UNIGRAM_LEN if is_list else MIN_DERIVED_UNIGRAM_LEN
                if len(tokens[0]) >= min_len:
                    finalized = self._finalize_term(tokens[0])
                    if finalized:
                        stage1.append(finalized)
                continue
            full_phrase = " ".join(tokens)
            finalized = self._finalize_term(full_phrase)
            if finalized:
                stage1.append(finalized)
            extra = [p for p in _multiword_from_tokens(tokens) if p != full_phrase]
            strong, broader = _split_strong_and_broader_phrases(extra)
            stage1.extend(self._finalize_terms(strong, allow_fuzzy=False))
            stage2.extend(self._finalize_terms(broader, allow_fuzzy=False))

    def _collect_from_slash_path(
        self,
        source: str,
        stage1: list[str],
        stage2: list[str],
        stage3: list[str],
    ) -> None:
        segments = _slash_segments(source)
        for index, segment in enumerate(segments):
            tokens = _tokenize(segment)
            if not tokens:
                continue
            is_leaf = index == len(segments) - 1
            if len(tokens) >= 2:
                if len(tokens) == 2 and not _keep_bigram(tokens[0], tokens[1]):
                    continue
                full_phrase = " ".join(tokens)
                finalized = self._finalize_term(full_phrase)
                if finalized:
                    stage1.append(finalized)
                extra = [p for p in _multiword_from_tokens(tokens) if p != full_phrase]
                strong, broader = _split_strong_and_broader_phrases(extra)
                stage1.extend(self._finalize_terms(strong, allow_fuzzy=False))
                stage2.extend(self._finalize_terms(broader, allow_fuzzy=False))
                continue
            finalized = self._finalize_term(tokens[0])
            if not finalized or len(tokens[0]) < MIN_DERIVED_UNIGRAM_LEN:
                continue
            if is_leaf:
                stage2.append(finalized)
            else:
                stage3.append(finalized)

    def _finalize_terms(self, terms: list[str], *, allow_fuzzy: bool = True) -> list[str]:
        result: list[str] = []
        for term in terms:
            finalized = self._finalize_term(term, allow_fuzzy=allow_fuzzy)
            if finalized:
                result.append(finalized)
        return result

    def _finalize_term(self, text: str, *, allow_fuzzy: bool = True) -> str:
        cleaned = basic_cleanup(text)
        if not cleaned:
            return ""
        # RapidFuzz runs on the segment/phrase, never on a long taxonomy label.
        if _is_structured_label(cleaned) or not allow_fuzzy:
            return " ".join(cleaned.lower().split())
        result = self.normalize_keyword(cleaned)
        if result.matched and result.normalized:
            return result.normalized
        return " ".join(cleaned.lower().split())


_service_instance: KeywordNormalizationService | None = None


def get_keyword_normalization_service() -> KeywordNormalizationService:
    global _service_instance
    if _service_instance is None:
        _service_instance = KeywordNormalizationService()
    return _service_instance


def reset_keyword_normalization_service() -> None:
    """Reset the process-local singleton (used in tests)."""
    global _service_instance
    _service_instance = None


async def ensure_keyword_vocabulary_loaded(session: AsyncSession) -> None:
    await get_keyword_normalization_service().ensure_loaded(session)


async def refresh_keyword_vocabulary_from_db(session: AsyncSession) -> None:
    await get_keyword_normalization_service().refresh_from_db(session)


def normalize_extracted_keywords_for_spotlight(
    extracted_keywords: dict[str, Any] | None,
) -> dict[str, Any]:
    return get_keyword_normalization_service().normalize_extracted_keywords(
        extracted_keywords
    )


def normalize_profile_field(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    if not cleaned:
        return None
    return get_keyword_normalization_service().normalize_keyword(cleaned).normalized


def prepare_search_terms_from_keywords(
    raw_keywords: list[str] | tuple[str, ...] | None,
) -> PreparedSearchTerms:
    """Public entry point for structural search-term preparation."""
    return get_keyword_normalization_service().prepare_search_terms(raw_keywords or [])


def extract_topic_concepts(topic: str) -> list[str]:
    """Turn one verbose profile/category label into concise search concepts.

    Each interest stays a small AND-group later. This does not emit the raw
    label, does not OR every token, and does not use a noise-word taxonomy.
    """
    cleaned = basic_cleanup(topic)
    if not cleaned:
        return []
    service = get_keyword_normalization_service()
    concepts: list[str] = []
    for segment in _split_topic_segments(cleaned):
        concepts.extend(_concepts_from_segment(service, segment))
    return _dedupe_terms(concepts)[:MAX_CONCEPTS_PER_GROUP]


def _split_topic_segments(text: str) -> list[str]:
    """Split a label on in / comma / slash / and, keeping each piece intact."""
    head, tail = _split_head_and_tail(text)
    chunks = [head]
    if tail:
        chunks.append(tail)
    segments: list[str] = []
    for chunk in chunks:
        for slash_part in _slash_segments(chunk):
            comma_items = _comma_segments(slash_part) or [slash_part]
            for item in comma_items:
                segments.extend(_and_segments(item))
    return [segment for segment in segments if segment.strip()]


def _and_segments(text: str) -> list[str]:
    pieces = [part.strip() for part in _AND_SPLIT_RE.split(text or "") if part.strip()]
    return pieces or ([text.strip()] if text and text.strip() else [])


def _is_level_modifier(token: str) -> bool:
    """True for leading degree/level adjectives (applied, advanced, …)."""
    if len(token) < 6:
        return False
    return token.endswith(_LEVEL_MODIFIER_SUFFIXES)


def _concepts_from_segment(
    service: KeywordNormalizationService,
    segment: str,
) -> list[str]:
    tokens = _tokenize(segment)
    if not tokens:
        return []
    if len(tokens) >= 3 and _is_level_modifier(tokens[0]):
        tokens = tokens[1:]
    if len(tokens) >= 2 and tokens[-1] in _CATEGORY_CONTAINER_WORDS:
        tokens = tokens[:-1]
    if not tokens:
        return []
    if len(tokens) == 1:
        finalized = service._finalize_term(tokens[0])
        return [finalized] if finalized else []
    phrase = " ".join(tokens)
    allow_fuzzy = len(tokens) <= 3 and not _is_structured_label(phrase)
    finalized = service._finalize_term(phrase, allow_fuzzy=allow_fuzzy)
    return [finalized] if finalized else []


def canonicalize_signal(value: str | None, *, fuzzy: bool = True) -> str:
    """Canonicalize a user signal without structural splitting.

    ``fuzzy=False`` is a dict lookup only (used for high-cardinality scoring
    fields). ``fuzzy=True`` allows RapidFuzz typo-correction for short fields
    such as major/minor/interests.
    """
    if value is None:
        return ""
    cleaned = str(value).strip()
    if not cleaned:
        return ""
    result = get_keyword_normalization_service().normalize_keyword(
        cleaned, fuzzy=fuzzy
    )
    return result.normalized or cleaned


def basic_cleanup(text: str) -> str:
    """Safe generic cleanup: trim, collapse whitespace, normalize separator spacing."""
    cleaned = str(text or "").replace("\u00a0", " ").strip()
    if not cleaned:
        return ""
    cleaned = _WHITESPACE_RE.sub(" ", cleaned)
    cleaned = _SLASH_SPACING_RE.sub("/", cleaned)
    cleaned = _COMMA_SPACING_RE.sub(", ", cleaned)
    return cleaned


def _is_structured_label(text: str) -> bool:
    return "/" in text or "," in text


def _split_head_and_tail(text: str) -> tuple[str, str]:
    match = _IN_SPLIT_RE.search(text)
    if not match:
        return text, ""
    head = text[: match.start()].strip()
    tail = text[match.end() :].strip()
    return head, tail


def _comma_segments(text: str) -> list[str]:
    if "," not in text:
        return []
    segments: list[str] = []
    for raw in text.split(","):
        item = _LEADING_AND_RE.sub("", raw.strip()).strip()
        if item:
            segments.append(item)
    return segments


def _slash_segments(text: str) -> list[str]:
    if "/" not in text:
        return [text] if text.strip() else []
    return [part.strip() for part in text.split("/") if part.strip()]


def _tokenize(text: str) -> list[str]:
    return [match.group(0).lower() for match in _TOKEN_RE.finditer(text)]


def _word_count(text: str) -> int:
    return len(_tokenize(text))


def _keep_bigram(left: str, right: str) -> bool:
    if not left or not right:
        return False
    longer = max(len(left), len(right))
    shorter = min(len(left), len(right))
    if longer < MIN_BIGRAM_LONGER_TOKEN_LEN:
        return False
    if shorter >= 4:
        return True
    # Allow a long modifier plus a short head noun, but not a short first token.
    return len(left) >= MIN_BIGRAM_LONGER_TOKEN_LEN and len(right) >= 3


def _multiword_from_tokens(tokens: list[str]) -> list[str]:
    if len(tokens) < 2:
        return []
    phrases: list[str] = [" ".join(tokens)]
    if len(tokens) >= 3:
        trailing = tokens[-2:]
        if _keep_bigram(trailing[0], trailing[1]):
            phrases.append(" ".join(trailing))
        for index in range(len(tokens) - 1):
            left, right = tokens[index], tokens[index + 1]
            if _keep_bigram(left, right):
                phrases.append(f"{left} {right}")
    return _dedupe_terms(phrases)[:MAX_PHRASES_PER_KEYWORD]


def _unigrams_from_tokens(tokens: list[str]) -> list[str]:
    return [token for token in tokens if len(token) >= MIN_DERIVED_UNIGRAM_LEN]


def _dedupe_terms(terms: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for term in terms:
        cleaned = " ".join(str(term).split())
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def refine_query_terms(
    terms: list[str] | tuple[str, ...] | None,
    *,
    limit: int | None = None,
) -> tuple[str, ...]:
    """Dedupe, drop unigrams already covered by a phrase, then cap query terms.

    Does not use a noise-word list or academic taxonomy. Longer phrases are
    kept; a unigram is omitted when it already appears inside a multi-word term.
    """
    from apps.learningspotlight.config import settings as spotlight_settings

    cap = limit if limit is not None else spotlight_settings.learning_spotlight_max_query_terms
    deduped = _dedupe_terms(list(terms or []))
    without_overlap = _drop_overlapping_unigrams(deduped)
    return tuple(_cap_terms(without_overlap, cap))


def _drop_overlapping_unigrams(terms: list[str]) -> list[str]:
    """Remove single-token terms that are already present in a multi-word phrase."""
    phrase_tokens: list[set[str]] = []
    for term in terms:
        tokens = _tokenize(term)
        if len(tokens) >= 2:
            phrase_tokens.append(set(tokens))
    if not phrase_tokens:
        return list(terms)

    result: list[str] = []
    for term in terms:
        tokens = _tokenize(term)
        if len(tokens) == 1 and any(tokens[0] in phrase for phrase in phrase_tokens):
            continue
        result.append(term)
    return result


def _split_strong_and_broader_phrases(phrases: list[str]) -> tuple[list[str], list[str]]:
    strong: list[str] = []
    broader: list[str] = []
    for phrase in phrases:
        tokens = _tokenize(phrase)
        if len(tokens) == 2 and _keep_bigram(tokens[0], tokens[1]):
            strong.append(phrase)
        else:
            broader.append(phrase)
    return strong, broader


def _term_quality(term: str) -> tuple:
    """Lower tuples are preferred. Structural heuristic, not a domain taxonomy."""
    tokens = _tokenize(term)
    n = len(tokens)
    if n == 2 and _keep_bigram(tokens[0], tokens[1]) and len(tokens[0]) >= 4:
        return (0, -sum(len(token) for token in tokens))
    if n == 2:
        return (1, -max(len(token) for token in tokens))
    if n == 1:
        return (2, -len(tokens[0]))
    if n == 3:
        return (3, n)
    return (4, n)


def _cap_terms(terms: list[str], limit: int) -> list[str]:
    deduped = _dedupe_terms(terms)
    if len(deduped) <= limit:
        return deduped
    ranked = sorted(
        enumerate(deduped),
        key=lambda item: (*_term_quality(item[1]), item[0]),
    )
    selected_keys = {term.casefold() for _index, term in ranked[:limit]}
    return [term for term in deduped if term.casefold() in selected_keys][:limit]


def _comparison_form(text: str) -> str:
    cleaned = text.strip().lower()
    cleaned = re.sub(r"[-_/]+", " ", cleaned)
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    return " ".join(cleaned.split())


def _length_ratio_ok(comparison_form: str, canonical: str) -> bool:
    left = len(comparison_form)
    right = len(_comparison_form(canonical))
    if left == 0 or right == 0:
        return False
    return min(left, right) / max(left, right) >= MIN_LENGTH_RATIO


def _accept_fuzzy_match(
    *,
    score: float,
    second_score: float,
    comparison_form: str,
    canonical: str,
) -> bool:
    if not _length_ratio_ok(comparison_form, canonical):
        return False

    if len(comparison_form) <= SHORT_KEYWORD_MAX_LEN:
        return score >= SHORT_KEYWORD_MIN_SCORE and (score - second_score) >= MIN_SCORE_GAP

    if score >= STRONG_MATCH_THRESHOLD:
        return (score - second_score) >= MIN_SCORE_GAP

    if score >= ACCEPTABLE_MATCH_THRESHOLD:
        return (score - second_score) >= MIN_SCORE_GAP

    if score >= LOW_MATCH_THRESHOLD:
        if score < SAFE_LOW_BAND_MIN_SCORE:
            return False
        return (score - second_score) >= SAFE_LOW_BAND_MIN_GAP

    return False
