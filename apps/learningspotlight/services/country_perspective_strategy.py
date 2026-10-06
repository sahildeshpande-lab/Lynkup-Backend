"""Country Perspective strategy — research from the student's country/region and field.

Product intent:
For a CS student in India, surface Computer Science research by a
researcher/professor affiliated with that country/region.

Implementation approach (candidate generation):
1. Select bounded, prioritized concepts from major → minor → interests
   (Country Perspective-specific hierarchical extraction for interests).
2. Build one OR query that AND-pairs each concept with the user's country.
3. Call the shared Semantic Scholar search helper with ``override_queries``
   and ``fields_of_study=None`` (country is a query signal only).
4. Convert raw results to ``SpotlightCandidate`` models.

LIMITATIONS
-----------
* Semantic Scholar does **not** support a dedicated country/institution
  filter on ``/paper/search/bulk``.  Country relevance is achieved by
  **query-based approximation** — appending the country name to the search
  query.  This biases results but does not guarantee the authors are
  affiliated with that country.
* Author ``affiliations`` are requested in the V2 field set and stored in
  candidate metadata when present, enabling a future post-filter step.
* If the user has no ``country`` set on their ``SpotlightUserContext``, the
  strategy falls back to a plain field-based search (no country bias).
* Country must come from ``profiles.country_id -> countries.name`` (loaded
  into ``SpotlightUserContext.country`` by the daily generator), never from
  ``extracted_keywords``.
"""

from __future__ import annotations

import logging
from typing import Any

from apps.learningspotlight.config import settings as spotlight_settings
from apps.learningspotlight.schemas import SpotlightCandidate, SpotlightUserContext
from apps.learningspotlight.services import semantic_scholar_adapter
from apps.learningspotlight.services.query_helpers import (
    build_country_perspective_query,
    encode_spotlight_query,
    has_usable_open_access_pdf,
    raw_papers_to_candidates,
    search_spotlight_candidates,
    select_country_perspective_concepts,
)
from apps.learningspotlight.services.semantic_scholar_adapter import (
    SemanticScholarExternalError,
)
from apps.learningspotlight.services.spotlight_strategy import (
    SpotlightStrategy,
    resolve_spotlight_search_limit,
)
from common.enums import SpotlightType


logger = logging.getLogger(__name__)


class CountryPerspectiveStrategy(SpotlightStrategy):
    """Surface research relevant to the user's academic field *and* country."""

    spotlight_type = SpotlightType.country_perspective

    async def get_candidates(
        self,
        context: SpotlightUserContext,
    ) -> list[SpotlightCandidate]:
        selected_concepts = select_country_perspective_concepts(
            context.extracted_keywords,
        )
        if not selected_concepts:
            logger.info(
                "[country-perspective] user_id=%s country=%r selected_concepts=[] "
                "query='' status=None result_count=0 accepted_count=0 "
                "fields_of_study=None reason=no_searchable_keywords",
                context.user_id,
                context.country,
            )
            return []

        query = build_country_perspective_query(
            selected_concepts,
            country=context.country,
        )
        if not query:
            logger.info(
                "[country-perspective] user_id=%s country=%r selected_concepts=%r "
                "query='' status=None result_count=0 accepted_count=0 "
                "fields_of_study=None reason=empty_query",
                context.user_id,
                context.country,
                selected_concepts,
            )
            return []

        search_meta: dict[str, Any] = {
            "status": None,
            "result_count": 0,
            "attempt": 0,
            "retry_delay": None,
        }

        async def _tracked_search(
            search_query: str,
            **kwargs: Any,
        ) -> tuple[dict[str, Any], int | None]:
            search_meta["attempt"] = int(search_meta["attempt"]) + 1
            try:
                payload, status = await semantic_scholar_adapter.search_papers_v2(
                    search_query,
                    **kwargs,
                )
            except SemanticScholarExternalError:
                search_meta["status"] = None
                raise
            search_meta["status"] = status
            search_meta["result_count"] = len(payload.get("data") or [])
            return payload, status

        limit = resolve_spotlight_search_limit(context)
        try:
            papers, query = await search_spotlight_candidates(
                context.extracted_keywords,
                search_fn=_tracked_search,
                fields_of_study=None,
                limit=limit,
                user_id=context.user_id,
                log_prefix="country-perspective",
                override_queries=[query],
                country=context.country,
            )
        except SemanticScholarExternalError as exc:
            logger.error(
                "[country-perspective] user_id=%s country=%r selected_concepts=%r "
                "query=%r encoded_query=%r fields_of_study=None "
                "request_attempt=%s status=%s result_count=external_failure "
                "accepted_count=0 error=%s",
                context.user_id,
                context.country,
                selected_concepts,
                query,
                encode_spotlight_query(query),
                search_meta["attempt"],
                exc.status_code,
                exc.message,
            )
            raise

        rejection_counts = _count_paper_rejections(papers)
        accepted_count = len(papers) - sum(rejection_counts.values())

        logger.info(
            "[country-perspective] user_id=%s country=%r selected_concepts=%r "
            "query=%r encoded_query=%r fields_of_study=None "
            "request_attempt=%s status=%s retry_delay=%s "
            "result_count=%s accepted_count=%s rejection_counts=%s",
            context.user_id,
            context.country,
            selected_concepts,
            query,
            encode_spotlight_query(query) if query else "",
            search_meta["attempt"],
            search_meta["status"],
            search_meta["retry_delay"],
            search_meta["result_count"],
            accepted_count,
            rejection_counts,
        )

        extra_metadata: dict[str, Any] = {}
        if context.country:
            extra_metadata["country"] = context.country

        candidates = raw_papers_to_candidates(
            papers,
            query=query,
            spotlight_type=SpotlightType.country_perspective,
            extra_metadata=extra_metadata,
        )

        for paper, candidate in zip(papers, candidates):
            affiliations = _extract_affiliations(paper)
            if affiliations:
                candidate.metadata["author_affiliations"] = affiliations

        logger.info(
            "[country-perspective] user_id=%s country=%r final_accepted_count=%d",
            context.user_id,
            context.country,
            len(candidates),
        )
        return candidates


def _count_paper_rejections(papers: list[dict[str, Any]]) -> dict[str, int]:
    """Tally why raw SS papers would be dropped by ``raw_papers_to_candidates``."""
    counts = {
        "missing_paper_id": 0,
        "missing_abstract": 0,
        "unusable_open_access_pdf": 0,
    }
    for paper in papers:
        if not isinstance(paper, dict):
            counts["missing_paper_id"] += 1
            continue
        if not paper.get("paperId"):
            counts["missing_paper_id"] += 1
            continue
        abstract = paper.get("abstract")
        if not abstract or not str(abstract).strip():
            counts["missing_abstract"] += 1
            continue
        if not has_usable_open_access_pdf(paper):
            counts["unusable_open_access_pdf"] += 1
    return counts


def _extract_affiliations(paper: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract author affiliations from a raw SS paper dict."""
    authors = paper.get("authors")
    if not authors or not isinstance(authors, list):
        return []

    result: list[dict[str, Any]] = []
    for author in authors:
        if not isinstance(author, dict):
            continue
        affs = author.get("affiliations")
        if affs:
            result.append({
                "author_id": author.get("authorId"),
                "name": author.get("name"),
                "affiliations": affs,
            })
    return result
