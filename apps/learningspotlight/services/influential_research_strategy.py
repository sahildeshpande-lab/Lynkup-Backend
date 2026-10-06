"""Influential Research strategy — high-impact papers in the user's field.

Product definition:
*Influential* means ``citation_count >= MIN_INFLUENTIAL_CITATIONS`` (10).

Implementation approach (Step 6 — candidate generation only):
1. Build a Boolean query from the user's ``extracted_keywords``.
2. Call V2 Semantic Scholar adapter with **no year restriction** (influential
   papers can be from any era).
3. Request a larger batch (limit=50) to have enough candidates after
   filtering.
4. **Filter out** papers with ``citation_count < MIN_INFLUENTIAL_CITATIONS``.
5. Return remaining candidates with ``citation_count`` preserved.

No final ranking or scoring is applied — that is a later step.
"""

from __future__ import annotations

import logging
from typing import Any

from apps.learningspotlight.schemas import SpotlightCandidate, SpotlightUserContext
from apps.learningspotlight.services import semantic_scholar_adapter
from apps.learningspotlight.services.query_helpers import (
    encode_spotlight_query,
    raw_papers_to_candidates,
    search_spotlight_candidates,
)
from apps.learningspotlight.services.spotlight_strategy import (
    SpotlightStrategy,
    resolve_spotlight_search_limit,
)
from common.enums import SpotlightType

logger = logging.getLogger(__name__)

# Product threshold: a paper is considered influential when it has
# at least this many citations.
MIN_INFLUENTIAL_CITATIONS = 10


class InfluentialResearchStrategy(SpotlightStrategy):
    """Surface highly-cited research relevant to the user's field."""

    spotlight_type = SpotlightType.influential_research

    async def get_candidates(
        self,
        context: SpotlightUserContext,
    ) -> list[SpotlightCandidate]:
        limit = resolve_spotlight_search_limit(context)
        papers, query = await search_spotlight_candidates(
            context.extracted_keywords,
            search_fn=semantic_scholar_adapter.search_papers_v2,
            fields_of_study=None,
            limit=limit,
            user_id=context.user_id,
            log_prefix="influential-research",
        )

        if not query:
            logger.info(
                "[influential-research] No searchable keywords for user %s",
                context.user_id,
            )
            return []

        logger.info(
            "[influential-research] user=%s query=%r fields_of_study=%s encoded_query=%r",
            context.user_id,
            query,
            None,
            encode_spotlight_query(query) if query else "",
        )

        all_candidates = raw_papers_to_candidates(
            papers,
            query=query,
            spotlight_type=SpotlightType.influential_research,
            extra_metadata={"min_citation_threshold": MIN_INFLUENTIAL_CITATIONS},
        )

        # Apply the product citation threshold filter.
        eligible = [
            c
            for c in all_candidates
            if (c.citation_count or 0) >= MIN_INFLUENTIAL_CITATIONS
        ]

        logger.info(
            "[influential-research] user=%s  raw=%d  eligible=%d  "
            "(threshold=%d)",
            context.user_id,
            len(all_candidates),
            len(eligible),
            MIN_INFLUENTIAL_CITATIONS,
        )
        return eligible
