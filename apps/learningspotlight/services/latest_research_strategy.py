"""Latest Research strategy — recent papers in the user's field.

Product definition:
*Recent* means published within approximately the last 1–2 years.

Implementation approach (Step 6 — candidate generation only):
1. Build a Boolean query from the user's ``extracted_keywords``.
2. Compute a **dynamic** year filter based on the current date:
   ``f"{current_year - 1}-"`` — this gives papers from the previous
   calendar year onwards (approximately 1–2 years of coverage).
3. Call V2 Semantic Scholar adapter with the computed year filter.
4. Convert raw results to ``SpotlightCandidate`` models with ``year``
   preserved.

Example:
    Current date = 2026-08-23 → year filter = "2025-"
    This returns papers published in 2025 or 2026.

No final ranking or scoring is applied — that is a later step.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

from apps.learningspotlight.config import settings as spotlight_settings
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

# How many calendar years back to include.  The year filter is computed as
# ``current_year - LATEST_RESEARCH_MAX_AGE_YEARS`` (inclusive).
LATEST_RESEARCH_MAX_AGE_YEARS = 1


def _compute_year_filter(today: date | None = None) -> str:
    """Return a Semantic Scholar year filter string for recent papers.

    Format: ``"<start_year>-"`` which means "from start_year onwards".

    Example: today = 2026-08-23 → ``"2025-"``
    """
    if today is None:
        today = datetime.now(timezone.utc).date()
    start_year = today.year - LATEST_RESEARCH_MAX_AGE_YEARS
    return f"{start_year}-"


class LatestResearchStrategy(SpotlightStrategy):
    """Surface recently-published research relevant to the user's field."""

    spotlight_type = SpotlightType.latest_research

    async def get_candidates(
        self,
        context: SpotlightUserContext,
    ) -> list[SpotlightCandidate]:
        year_filter = _compute_year_filter()
        limit = resolve_spotlight_search_limit(context)
        papers, query = await search_spotlight_candidates(
            context.extracted_keywords,
            search_fn=semantic_scholar_adapter.search_papers_v2,
            fields_of_study=None,
            year=year_filter,
            limit=limit,
            user_id=context.user_id,
            log_prefix="latest-research",
        )

        if not query:
            logger.info(
                "[latest-research] No searchable keywords for user %s",
                context.user_id,
            )
            return []

        logger.info(
            "[latest-research] user=%s query=%r fields_of_study=%s year_filter=%s "
            "encoded_query=%r",
            context.user_id,
            query,
            None,
            year_filter,
            encode_spotlight_query(query) if query else "",
        )

        candidates = raw_papers_to_candidates(
            papers,
            query=query,
            spotlight_type=SpotlightType.latest_research,
            extra_metadata={"year_filter": year_filter},
        )

        logger.info(
            "[latest-research] user=%s  candidates=%d  year_filter=%s",
            context.user_id,
            len(candidates),
            year_filter,
        )
        return candidates
