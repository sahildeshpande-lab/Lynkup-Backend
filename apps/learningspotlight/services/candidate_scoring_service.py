"""Learning Spotlight – Candidate Scoring & Ranking Service.

Calculates normalized 0–100 component scores, combines them into a weighted
final score, ranks candidates with deterministic tie-breaking, and selects
the single best candidate.

Component scores
----------------
1. **User relevance (40%)**:
   Deterministic keyword matching between candidate text (title/abstract) and
   user signals (major, minor, interests, engagement_keywords, content_keywords,
   hashtags) with higher weight for high-signal sources and accumulated
   engagement counts.
2. **Paper quality (20%)**:
   Evaluates metadata completeness (title, abstract, authors, venue) and a
   normalized citation component.
3. **Recency (10%)**:
   Evaluates publication year relative to current date (dynamic, no hardcoded year).
4. **Category-specific score (30%)**:
   * *Country Perspective*: rewards country/affiliation match with user's country.
   * *Influential Research*: logarithmic normalization of citation counts.
   * *Latest Research*: fine-grained differentiation among recently published papers.
   * *Beyond Your Field*: rewards divergence from major/minor while retaining interest overlap.
   * *Leading Thinker*: NOT IMPLEMENTED (raises NotImplementedError).

Tie-breaking order
------------------
1. Higher final score (DESC)
2. Higher user relevance score (DESC)
3. Higher category score (DESC)
4. Higher citation count (DESC)
5. Newer publication year (DESC)
6. Normalized paper_id ascending (ASC — final deterministic fallback)
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Sequence

from apps.learningspotlight.config import settings as spotlight_settings
from apps.learningspotlight.schemas import (
    ScoredSpotlightCandidate,
    SpotlightCandidate,
    SpotlightRankingResult,
    SpotlightUserContext,
)
from apps.learningspotlight.services.keyword_normalization_service import (
    canonicalize_signal,
)
from apps.recommendations.services.keyword_scoring import coerce_keyword_scores
from common.enums import SpotlightType

logger = logging.getLogger(__name__)

# Default recency score assigned to papers without a usable publication year.
DEFAULT_NO_YEAR_RECENCY_SCORE = 10.0


def _normalize_text(text: str | None) -> str:
    """Normalize text to lowercase with punctuation replaced by single spaces."""
    if not text:
        return ""
    # Replace non-alphanumeric characters with space and collapse whitespace
    cleaned = re.sub(r"[^\w\s]", " ", text.lower())
    return " ".join(cleaned.split())


def _keyword_in_text(keyword: str, normalized_text: str) -> bool:
    """Check if a normalized keyword or phrase appears in normalized text."""
    norm_kw = _normalize_text(keyword)
    if not norm_kw:
        return False
    # Exact word/phrase boundary match in space-delimited text
    pattern = r"(?:^|\s)" + re.escape(norm_kw) + r"(?:\s|$)"
    return bool(re.search(pattern, normalized_text))


def _as_string_list(value: Any) -> list[str]:
    """Convert scalar, None, or list of strings to cleaned list of non-empty strings."""
    if value is None:
        return []
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if item and str(item).strip()]
    return []


def _canonical_signals(values: list[str], *, fuzzy: bool = True) -> list[str]:
    """Canonicalize user signals without structural query expansion."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        canonical = canonicalize_signal(value, fuzzy=fuzzy) or value
        key = canonical.casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(canonical)
    return result


@dataclass(frozen=True, slots=True)
class _CachedRelevanceSignals:
    major_list: list[str]
    minor_list: list[str]
    interests_list: list[str]
    engagement_items: tuple[tuple[str, float], ...]
    content_items: tuple[tuple[str, float], ...]
    hashtag_items: tuple[tuple[str, float], ...]


class CandidateScoringService:
    """Service for scoring and ranking candidate papers."""

    def __init__(self) -> None:
        self._relevance_signals_key: int | None = None
        self._relevance_signals: _CachedRelevanceSignals | None = None

    def score_and_rank_candidates(
        self,
        candidates: Sequence[SpotlightCandidate],
        context: SpotlightUserContext,
        *,
        reference_date: date | None = None,
    ) -> SpotlightRankingResult:
        """Score all candidates, rank them deterministically, and select #1.

        Parameters
        ----------
        candidates:
            Filtered candidates from Step 7.
        context:
            User learning signals.
        reference_date:
            Target date for dynamic year/recency calculations (default: UTC today).

        Returns
        -------
        SpotlightRankingResult
            Contains ranked list of ScoredSpotlightCandidate and the selected #1.
        """
        if reference_date is None:
            reference_date = datetime.now(timezone.utc).date()

        scored_candidates: list[ScoredSpotlightCandidate] = []

        for candidate in candidates:
            user_relevance = self.calculate_user_relevance(candidate, context)
            quality = self.calculate_paper_quality(candidate)
            recency = self.calculate_recency_score(candidate, reference_date)
            category = self.calculate_category_score(candidate, context, reference_date)

            final_score = self.calculate_final_score(
                user_relevance=user_relevance,
                quality=quality,
                recency=recency,
                category=category,
            )

            scored = ScoredSpotlightCandidate(
                paper_id=candidate.paper_id,
                title=candidate.title,
                authors=candidate.authors,
                abstract=candidate.abstract,
                venue=candidate.venue,
                year=candidate.year,
                citation_count=candidate.citation_count,
                url=candidate.url,
                query=candidate.query,
                spotlight_type=candidate.spotlight_type,
                metadata=candidate.metadata,
                user_relevance_score=user_relevance,
                quality_score=quality,
                recency_score=recency,
                category_score=category,
                final_score=final_score,
            )
            scored_candidates.append(scored)

        # Sort descending with deterministic tie-breaking
        ranked = self._rank_candidates(scored_candidates)
        selected = ranked[0] if ranked else None

        return SpotlightRankingResult(
            ranked_candidates=ranked,
            selected_candidate=selected,
            total_candidates=len(ranked),
        )

    def calculate_user_relevance(
        self,
        candidate: SpotlightCandidate,
        context: SpotlightUserContext,
    ) -> float:
        """Calculate user relevance score (0–100) based on keyword matching."""
        title_norm = _normalize_text(candidate.title)
        abstract_norm = _normalize_text(candidate.abstract)
        full_text = f"{title_norm} {abstract_norm}".strip()

        if not full_text:
            return 0.0

        signals = self._relevance_signals_for(context)

        # 1. Major signals
        major_score = 0.0
        for m in signals.major_list:
            if _keyword_in_text(m, full_text):
                major_score += 30.0
                if _keyword_in_text(m, title_norm):
                    major_score += 10.0  # Title match boost
                break  # Count primary major match once
        major_score = min(40.0, major_score)

        # 2. Minor signals
        minor_score = 0.0
        for m in signals.minor_list:
            if _keyword_in_text(m, full_text):
                minor_score += 20.0
                if _keyword_in_text(m, title_norm):
                    minor_score += 5.0
                break
        minor_score = min(25.0, minor_score)

        # 3. Interests signals
        interests_score = 0.0
        for interest in signals.interests_list:
            if _keyword_in_text(interest, full_text):
                interests_score += 20.0
                if _keyword_in_text(interest, title_norm):
                    interests_score += 5.0
        interests_score = min(35.0, interests_score)

        # 4. Engagement keywords (high weight; score scales with accumulated actions)
        engagement_score = 0.0
        for match_kw, raw_count in signals.engagement_items:
            if _keyword_in_text(match_kw, full_text):
                points = min(15.0, 3.0 * raw_count)
                if _keyword_in_text(match_kw, title_norm):
                    points += 3.0
                engagement_score += points
        engagement_score = min(35.0, engagement_score)

        # 5. Content keywords (medium-high weight)
        content_score = 0.0
        for match_kw, raw_count in signals.content_items:
            if _keyword_in_text(match_kw, full_text):
                points = min(10.0, 2.0 * raw_count)
                if _keyword_in_text(match_kw, title_norm):
                    points += 2.0
                content_score += points
        content_score = min(20.0, content_score)

        # 6. Hashtags (medium weight)
        hashtag_score = 0.0
        for match_kw, raw_count in signals.hashtag_items:
            if _keyword_in_text(match_kw, full_text):
                hashtag_score += min(8.0, 1.5 * raw_count)
        hashtag_score = min(15.0, hashtag_score)

        total_points = (
            major_score
            + minor_score
            + interests_score
            + engagement_score
            + content_score
            + hashtag_score
        )

        return min(100.0, max(0.0, round(total_points, 2)))

    def _relevance_signals_for(self, context: SpotlightUserContext) -> _CachedRelevanceSignals:
        key = id(context)
        if self._relevance_signals is not None and self._relevance_signals_key == key:
            return self._relevance_signals

        kw_data = context.extracted_keywords or {}
        engagement_map = coerce_keyword_scores(kw_data.get("engagement_keywords"))
        content_map = coerce_keyword_scores(kw_data.get("content_keywords"))
        hashtag_map = coerce_keyword_scores(kw_data.get("hashtags"))
        signals = _CachedRelevanceSignals(
            major_list=_canonical_signals(
                _as_string_list(kw_data.get("major"))
                or ([context.major] if context.major else [])
            ),
            minor_list=_canonical_signals(
                _as_string_list(kw_data.get("minor"))
                or ([context.minor] if context.minor else [])
            ),
            interests_list=_canonical_signals(
                _as_string_list(kw_data.get("interests")) or (context.interests or [])
            ),
            engagement_items=tuple(
                (canonicalize_signal(eng_kw, fuzzy=False) or eng_kw, raw_count)
                for eng_kw, raw_count in engagement_map.items()
            ),
            content_items=tuple(
                (canonicalize_signal(cont_kw, fuzzy=False) or cont_kw, raw_count)
                for cont_kw, raw_count in content_map.items()
            ),
            hashtag_items=tuple(
                (canonicalize_signal(tag, fuzzy=False) or tag, raw_count)
                for tag, raw_count in hashtag_map.items()
            ),
        )
        self._relevance_signals_key = key
        self._relevance_signals = signals
        return signals

    def calculate_paper_quality(self, candidate: SpotlightCandidate) -> float:
        """Calculate metadata completeness and general paper quality (0–100)."""
        score = 0.0

        # Title presence (+15)
        if candidate.title and candidate.title.strip():
            score += 15.0

        # Abstract presence & substance (+25)
        if candidate.abstract and candidate.abstract.strip():
            score += 15.0
            if len(candidate.abstract.strip()) >= 100:
                score += 10.0

        # Authors metadata (+20)
        if candidate.authors:
            score += 15.0
            if len(candidate.authors) >= 2 or any(a.author_id for a in candidate.authors):
                score += 5.0

        # Venue presence (+15)
        if candidate.venue and candidate.venue.strip():
            score += 15.0

        # Citation quality component (+25 max, log scale)
        citations = candidate.citation_count or 0
        if citations > 0:
            # log10(1) = 0, log10(1001) ~ 3.0
            log_cite = math.log10(1 + citations)
            cite_pts = min(25.0, (log_cite / 3.0) * 25.0)
            score += cite_pts

        return min(100.0, max(0.0, round(score, 2)))

    def calculate_recency_score(
        self,
        candidate: SpotlightCandidate,
        reference_date: date,
    ) -> float:
        """Calculate publication recency score (0–100) using dynamic year math."""
        if candidate.year is None:
            return DEFAULT_NO_YEAR_RECENCY_SCORE

        current_year = reference_date.year
        age = current_year - candidate.year

        if age <= 0:
            return 100.0  # Current year or preprint
        elif age == 1:
            return 90.0   # 1 year old
        elif age == 2:
            return 75.0   # 2 years old
        elif age == 3:
            return 60.0   # 3 years old
        elif age == 4:
            return 45.0   # 4 years old
        elif age == 5:
            return 30.0   # 5 years old
        else:
            # Linear decay for older papers, minimum 5.0
            return max(5.0, round(100.0 - (age * 12.0), 2))

    def calculate_category_score(
        self,
        candidate: SpotlightCandidate,
        context: SpotlightUserContext,
        reference_date: date,
    ) -> float:
        """Calculate category-specific score (0–100)."""
        st = candidate.spotlight_type

        if st == SpotlightType.country_perspective:
            return self._calculate_country_perspective_score(candidate, context)
        elif st == SpotlightType.influential_research:
            return self._calculate_influential_research_score(candidate)
        elif st == SpotlightType.latest_research:
            return self._calculate_latest_research_score(candidate, reference_date)
        elif st == SpotlightType.beyond_your_field:
            return self._calculate_beyond_field_score(candidate, context)
        elif st == SpotlightType.leading_thinker:
            return self._calculate_leading_thinker_score(candidate, context)
        return 50.0

    def _calculate_leading_thinker_score(
        self,
        candidate: SpotlightCandidate,
        context: SpotlightUserContext,
    ) -> float:
        """Leading thinker score: author influence and field relevance (0-100)."""
        from apps.learningspotlight.services.leading_thinker_strategy import (
            calculate_thinker_score,
        )

        meta = candidate.metadata or {}
        if "thinker_score" in meta and isinstance(meta["thinker_score"], (int, float)):
            return float(meta["thinker_score"])

        author_influence = float(meta.get("author_influence_score", 50.0))
        relevance = self.calculate_user_relevance(candidate, context)
        return calculate_thinker_score(author_influence, relevance)

    def _calculate_country_perspective_score(
        self,
        candidate: SpotlightCandidate,
        context: SpotlightUserContext,
    ) -> float:
        """Country perspective score: rewards matches with user country."""
        user_country = (context.country or "").strip().lower()
        if not user_country:
            return 50.0  # Neutral when no country specified

        meta = candidate.metadata or {}
        affiliations = meta.get("author_affiliations") or []

        # Check explicit author affiliations
        if isinstance(affiliations, list) and affiliations:
            has_match = False
            for aff_item in affiliations:
                if isinstance(aff_item, dict):
                    aff_list = aff_item.get("affiliations") or []
                    for aff_str in aff_list:
                        if user_country in str(aff_str).lower():
                            has_match = True
                            break
            if has_match:
                return 100.0  # Strong verified country match

        # Query metadata match
        if meta.get("country") and str(meta["country"]).strip().lower() == user_country:
            return 80.0  # Country was included in query

        # Neutral fallback since search query was country-biased
        return 65.0

    def _calculate_influential_research_score(
        self,
        candidate: SpotlightCandidate,
    ) -> float:
        """Influential research score: normalized citation curve (0–100)."""
        citations = candidate.citation_count or 0
        if citations < 20:
            return max(0.0, round((citations / 20.0) * 40.0, 2))

        # Logarithmic normalization: 20 citations -> 50.0, 1000+ citations -> 100.0
        log_cite = math.log10(citations)
        log_min = math.log10(20)    # ~1.301
        log_max = math.log10(1000)  # 3.0
        normalized = (log_cite - log_min) / (log_max - log_min)
        score = 50.0 + (min(1.0, max(0.0, normalized)) * 50.0)
        return min(100.0, max(0.0, round(score, 2)))

    def _calculate_latest_research_score(
        self,
        candidate: SpotlightCandidate,
        reference_date: date,
    ) -> float:
        """Latest research score: fine-grained recency differentiation."""
        if candidate.year is None:
            return DEFAULT_NO_YEAR_RECENCY_SCORE

        current_year = reference_date.year
        age = current_year - candidate.year

        if age <= 0:
            return 100.0  # Published this year or preprint
        elif age == 1:
            return 85.0   # Published last year
        elif age == 2:
            return 65.0   # 2 years old
        else:
            return max(10.0, round(100.0 - (age * 25.0), 2))

    def _calculate_beyond_field_score(
        self,
        candidate: SpotlightCandidate,
        context: SpotlightUserContext,
    ) -> float:
        """Beyond Your Field score: rewards divergence from major/minor + interest overlap."""
        title_norm = _normalize_text(candidate.title)
        abstract_norm = _normalize_text(candidate.abstract)
        full_text = f"{title_norm} {abstract_norm}".strip()

        score = 50.0  # Baseline for candidates passing Step 7

        # 1. Divergence check: does paper match user's major/minor?
        user_major = canonicalize_signal(context.major) or (context.major or "").strip().lower()
        user_minor = canonicalize_signal(context.minor) or (context.minor or "").strip().lower()
        matches_major = bool(user_major and _keyword_in_text(user_major, full_text))
        matches_minor = bool(user_minor and _keyword_in_text(user_minor, full_text))

        if not matches_major and not matches_minor:
            score += 25.0  # Rewarded for being genuinely outside user's primary field
        else:
            score -= 20.0  # Penalized for overlapping user's primary field

        # 2. Interest / engagement overlap check: is it still intellectually relevant?
        interests = _canonical_signals(
            context.interests
            or _as_string_list((context.extracted_keywords or {}).get("interests"))
        )
        eng_keywords = _canonical_signals(
            list(
                coerce_keyword_scores(
                    (context.extracted_keywords or {}).get("engagement_keywords")
                ).keys()
            )
        )

        has_interest_overlap = any(_keyword_in_text(i, full_text) for i in interests)
        has_eng_overlap = any(_keyword_in_text(e, full_text) for e in eng_keywords)

        if has_interest_overlap or has_eng_overlap:
            score += 25.0

        return min(100.0, max(0.0, round(score, 2)))

    def calculate_final_score(
        self,
        *,
        user_relevance: float,
        quality: float,
        recency: float,
        category: float,
    ) -> float:
        """Compute final weighted score from normalized components."""
        final = (
            user_relevance * spotlight_settings.weight_user_relevance
            + quality * spotlight_settings.weight_paper_quality
            + recency * spotlight_settings.weight_recency
            + category * spotlight_settings.weight_category
        )
        return min(100.0, max(0.0, round(final, 2)))

    def _rank_candidates(
        self,
        scored_candidates: list[ScoredSpotlightCandidate],
    ) -> list[ScoredSpotlightCandidate]:
        """Rank scored candidates with deterministic tie-breaking."""
        def _sort_key(c: ScoredSpotlightCandidate) -> tuple:
            return (
                -c.final_score,
                -c.user_relevance_score,
                -c.category_score,
                -(c.citation_count if c.citation_count is not None else -1),
                -(c.year if c.year is not None else -1),
                c.paper_id or "",  # Ascending string order as final deterministic fallback
            )

        return sorted(scored_candidates, key=_sort_key)
