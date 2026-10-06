"""Leading Thinker strategy — research papers from influential scholars in the user's field.

Product Definition:
"Learn from a leading thinker in your field."
Surfaces peer-reviewed research associated with an academically impactful, highly-cited
researcher whose publications align with the student's primary academic field.

Disclaimer:
"Leading thinker" is modeled deterministically based on measurable research impact metrics
(citation volume, h-index, research output, and user field relevance). Semantic Scholar
data provides publication graphs and does not make subjective claims of thought leadership.

Algorithm Flow:
1. User Learning Data: Extract user topics from ``profile.extracted_keywords``.
2. Build Boolean Query: Focused on the user's field / major / interests.
3. Candidate Retrieval: Retrieve candidate papers via Semantic Scholar bulk search.
4. Extract Candidate Authors: Identify authors across candidate papers.
5. Batch Author Evaluation: Lookup author influence metrics (citations, h-index, publication count)
   via Semantic Scholar ``/author/batch`` (or cached evaluations).
6. Deduplication: Compute and cache author influence once per author per generation run.
7. Thinker Score Calculation: Combine normalized author influence (70%) and user paper relevance (30%).
8. Attach Thinker Metadata & Return ``SpotlightCandidate`` models for Step 7/8 filtering and ranking.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Callable

from apps.learningspotlight.config import settings as spotlight_settings
from apps.learningspotlight.schemas import (
    LearningSpotlightAuthor,
    SpotlightCandidate,
    SpotlightUserContext,
)
from apps.learningspotlight.services import semantic_scholar_adapter
from apps.learningspotlight.services.query_helpers import (
    encode_spotlight_query,
    raw_papers_to_candidates,
    search_spotlight_candidates,
)
from apps.learningspotlight.services.spotlight_strategy import SpotlightStrategy
from common.enums import SpotlightType

logger = logging.getLogger(__name__)

DEFAULT_MAX_AUTHORS_EVALUATED = 15


def calculate_author_influence_score(
    author_data: dict[str, Any] | None,
    *,
    paper_citation_count: int | None = None,
) -> float:
    """Calculate normalized author influence score (0-100).

    Signals:
    - Author Citation Count (50% default weight)
    - Author H-Index (30% default weight)
    - Author Paper Count / Research Output (20% default weight)

    Gracefully redistributes weights if any metric is missing.
    Falls back to paper citation signals if author metrics are unavailable.
    """
    if not author_data or not isinstance(author_data, dict):
        if paper_citation_count is not None and paper_citation_count > 0:
            log_c = math.log10(max(1, paper_citation_count))
            # 1 citation -> 10.0, 1000+ citations -> 80.0
            return min(80.0, max(10.0, round((log_c / 3.0) * 80.0, 2)))
        return 50.0  # Neutral baseline

    citations = author_data.get("citationCount")
    h_index = author_data.get("hIndex")
    paper_count = author_data.get("paperCount")

    components: list[tuple[float, float]] = []

    # 1. Author Citation Count (0-100 pts)
    if citations is not None and isinstance(citations, (int, float)):
        c_val = max(0, int(citations))
        if c_val == 0:
            c_score = 0.0
        elif c_val < 10:
            c_score = (c_val / 10.0) * 25.0
        else:
            # Logarithmic curve: 10 -> 25.0, 10,000+ -> 100.0
            log_c = math.log10(c_val)
            norm = (log_c - 1.0) / 3.0  # log10(10)=1.0, log10(10000)=4.0
            c_score = 25.0 + (min(1.0, max(0.0, norm)) * 75.0)
        components.append((min(100.0, max(0.0, c_score)), 0.50))

    # 2. Author H-Index (0-100 pts)
    if h_index is not None and isinstance(h_index, (int, float)):
        h_val = max(0, int(h_index))
        # H-index 40+ is considered elite / world-class scholar
        h_score = min(100.0, (h_val / 40.0) * 100.0)
        components.append((h_score, 0.30))

    # 3. Author Paper Count / Research Output (0-100 pts)
    if paper_count is not None and isinstance(paper_count, (int, float)):
        p_val = max(0, int(paper_count))
        # 100+ publications -> 100.0
        p_score = min(100.0, (p_val / 100.0) * 100.0)
        components.append((p_score, 0.20))

    if not components:
        if paper_citation_count is not None and paper_citation_count > 0:
            log_c = math.log10(max(1, paper_citation_count))
            return min(80.0, max(10.0, round((log_c / 3.0) * 80.0, 2)))
        return 50.0

    total_weight = sum(w for _, w in components)
    if total_weight <= 0.0:
        return 50.0

    final_score = sum(score * (w / total_weight) for score, w in components)
    return min(100.0, max(0.0, round(final_score, 2)))


def calculate_thinker_score(
    author_influence_score: float,
    paper_relevance_score: float,
    *,
    weight_author: float | None = None,
    weight_relevance: float | None = None,
) -> float:
    """Calculate combined Thinker score (0-100)."""
    w_auth = (
        weight_author
        if weight_author is not None
        else spotlight_settings.weight_thinker_author_influence
    )
    w_rel = (
        weight_relevance
        if weight_relevance is not None
        else spotlight_settings.weight_thinker_paper_relevance
    )
    score = (author_influence_score * w_auth) + (paper_relevance_score * w_rel)
    return min(100.0, max(0.0, round(score, 2)))


def _author_id(author: LearningSpotlightAuthor | None) -> str:
    if author is None or not author.author_id:
        return ""
    return str(author.author_id).strip()


def collect_unique_author_ids(candidates: list[SpotlightCandidate]) -> list[str]:
    """Stable unique author IDs in first-seen order."""
    seen: set[str] = set()
    ordered: list[str] = []
    for cand in candidates:
        for auth in cand.authors:
            aid = _author_id(auth)
            if not aid or aid in seen:
                continue
            seen.add(aid)
            ordered.append(aid)
    return ordered


def select_authors_for_evaluation(
    candidates: list[SpotlightCandidate],
    *,
    limit: int | None = None,
) -> list[str]:
    """Pick the strongest/relevant authors before the expensive /author/batch call.

    Cheap signals from papers already in hand (no extra HTTP):
    first-author appearances, max paper citations, co-occurrence count.
    """
    cap = (
        limit
        if limit is not None
        else getattr(
            spotlight_settings,
            "learning_spotlight_max_authors_evaluated",
            DEFAULT_MAX_AUTHORS_EVALUATED,
        )
    )
    stats: dict[str, dict[str, Any]] = {}
    for cand in candidates:
        citations = cand.citation_count or 0
        for index, auth in enumerate(cand.authors):
            aid = _author_id(auth)
            if not aid:
                continue
            entry = stats.get(aid)
            if entry is None:
                entry = {
                    "author_id": aid,
                    "appearances": 0,
                    "max_citations": 0,
                    "total_citations": 0,
                    "first_author_count": 0,
                }
                stats[aid] = entry
            entry["appearances"] += 1
            entry["max_citations"] = max(entry["max_citations"], citations)
            entry["total_citations"] += citations
            if index == 0:
                entry["first_author_count"] += 1

    ranked = sorted(
        stats.values(),
        key=lambda entry: (
            -int(entry["first_author_count"]),
            -int(entry["max_citations"]),
            -int(entry["appearances"]),
            -int(entry["total_citations"]),
            str(entry["author_id"]),
        ),
    )
    return [str(entry["author_id"]) for entry in ranked[: max(0, int(cap))]]


def _stage_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 1)


class LeadingThinkerStrategy(SpotlightStrategy):
    """Surface research from influential, highly-cited scholars in the user's field."""

    spotlight_type = SpotlightType.leading_thinker

    def __init__(
        self,
        *,
        search_fn: Callable[..., Any] | None = None,
        author_lookup_fn: Callable[..., Any] | None = None,
    ) -> None:
        self._search_fn = search_fn or semantic_scholar_adapter.search_papers_v2
        self._author_lookup_fn = (
            author_lookup_fn or semantic_scholar_adapter.get_authors_batch_v2
        )

    async def get_candidates(
        self,
        context: SpotlightUserContext,
    ) -> list[SpotlightCandidate]:
        from apps.learningspotlight.services.candidate_scoring_service import (
            CandidateScoringService,
        )

        started_all = time.perf_counter()
        from apps.learningspotlight.services.spotlight_strategy import (
            resolve_spotlight_search_limit,
        )

        limit = resolve_spotlight_search_limit(context)

        stage = time.perf_counter()
        papers, query = await search_spotlight_candidates(
            context.extracted_keywords,
            search_fn=self._search_fn,
            fields_of_study=None,
            limit=limit,
            user_id=context.user_id,
            log_prefix="leading-thinker",
        )
        logger.info(
            "[leading-thinker] user=%s stage=paper_search duration_ms=%.1f "
            "papers=%d query=%r fields_of_study=%s encoded_query=%r",
            context.user_id,
            _stage_ms(stage),
            len(papers),
            query,
            None,
            encode_spotlight_query(query) if query else "",
        )
        if not query:
            logger.info(
                "[leading-thinker] No searchable keywords for user %s",
                context.user_id,
            )
            logger.info(
                "[leading-thinker] user=%s stage=complete duration_ms=%.1f "
                "candidates=0 authors_found=0 authors_evaluated=0",
                context.user_id,
                _stage_ms(started_all),
            )
            return []

        if not papers:
            logger.info(
                "[leading-thinker] user=%s stage=complete duration_ms=%.1f "
                "candidates=0 authors_found=0 authors_evaluated=0",
                context.user_id,
                _stage_ms(started_all),
            )
            return []

        stage = time.perf_counter()
        all_candidates = raw_papers_to_candidates(
            papers,
            query=query,
            spotlight_type=SpotlightType.leading_thinker,
        )
        logger.info(
            "[leading-thinker] user=%s stage=paper_parsing duration_ms=%.1f "
            "raw_papers=%d parsed=%d",
            context.user_id,
            _stage_ms(stage),
            len(papers),
            len(all_candidates),
        )
        if not all_candidates:
            logger.info(
                "[leading-thinker] user=%s stage=complete duration_ms=%.1f "
                "candidates=0 authors_found=0 authors_evaluated=0",
                context.user_id,
                _stage_ms(started_all),
            )
            return []

        stage = time.perf_counter()
        unique_author_ids = collect_unique_author_ids(all_candidates)
        logger.info(
            "[leading-thinker] user=%s stage=unique_author_extraction "
            "duration_ms=%.1f unique_authors=%d",
            context.user_id,
            _stage_ms(stage),
            len(unique_author_ids),
        )

        stage = time.perf_counter()
        author_ids_list = select_authors_for_evaluation(all_candidates)
        logger.info(
            "[leading-thinker] user=%s stage=author_selection duration_ms=%.1f "
            "authors_found=%d authors_selected=%d",
            context.user_id,
            _stage_ms(stage),
            len(unique_author_ids),
            len(author_ids_list),
        )

        stage = time.perf_counter()
        authors_map: dict[str, dict[str, Any]] = {}
        if author_ids_list:
            authors_map = await self._author_lookup_fn(author_ids_list)
        logger.info(
            "[leading-thinker] user=%s stage=author_batch duration_ms=%.1f "
            "requested=%d returned=%d",
            context.user_id,
            _stage_ms(stage),
            len(author_ids_list),
            len(authors_map),
        )

        stage = time.perf_counter()
        author_score_cache: dict[str, float] = {}
        for author_id in author_ids_list:
            author_score_cache[author_id] = calculate_author_influence_score(
                authors_map.get(author_id)
            )
        logger.info(
            "[leading-thinker] user=%s stage=influence_calculation duration_ms=%.1f "
            "authors_scored=%d",
            context.user_id,
            _stage_ms(stage),
            len(author_score_cache),
        )

        scoring_service = CandidateScoringService()
        stage = time.perf_counter()
        for cand in all_candidates:
            best_author: LearningSpotlightAuthor | None = None
            best_author_influence = 50.0

            if cand.authors:
                scored_authors: list[tuple[LearningSpotlightAuthor, float]] = []
                for auth in cand.authors:
                    aid = _author_id(auth)
                    if aid and aid in author_score_cache:
                        auth_score = author_score_cache[aid]
                    else:
                        auth_score = calculate_author_influence_score(
                            None,
                            paper_citation_count=cand.citation_count,
                        )
                    scored_authors.append((auth, auth_score))

                scored_authors.sort(key=lambda item: item[1], reverse=True)
                best_author, best_author_influence = scored_authors[0]
            else:
                best_author_influence = calculate_author_influence_score(
                    None,
                    paper_citation_count=cand.citation_count,
                )

            paper_relevance = scoring_service.calculate_user_relevance(cand, context)
            thinker_score = calculate_thinker_score(
                best_author_influence,
                paper_relevance,
            )

            cand.metadata.update(
                {
                    "thinker_score": thinker_score,
                    "author_influence_score": best_author_influence,
                    "paper_relevance_score": paper_relevance,
                    "leading_author": (
                        {
                            "author_id": best_author.author_id,
                            "name": best_author.name,
                        }
                        if best_author
                        else None
                    ),
                }
            )
        logger.info(
            "[leading-thinker] user=%s stage=candidate_scoring duration_ms=%.1f "
            "candidates=%d",
            context.user_id,
            _stage_ms(stage),
            len(all_candidates),
        )

        stage = time.perf_counter()
        top_score = max(
            (float(c.metadata.get("thinker_score") or 0.0) for c in all_candidates),
            default=0.0,
        )
        logger.info(
            "[leading-thinker] user=%s stage=final_ranking duration_ms=%.1f "
            "candidates=%d top_thinker_score=%.2f",
            context.user_id,
            _stage_ms(stage),
            len(all_candidates),
            top_score,
        )

        logger.info(
            "[leading-thinker] user=%s stage=complete duration_ms=%.1f "
            "candidates=%d authors_found=%d authors_evaluated=%d query=%r",
            context.user_id,
            _stage_ms(started_all),
            len(all_candidates),
            len(unique_author_ids),
            len(author_ids_list),
            query,
        )
        return all_candidates
