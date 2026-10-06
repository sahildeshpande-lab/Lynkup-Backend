"""Beyond Your Field strategy — useful research outside the user's major/minor.

Product intent:
Broaden knowledge with papers from academic domains **different** from the
student's primary field of study.

Implementation approach (Step 6 — candidate generation only):
1. Identify the user's primary field: ``major`` (and ``minor``).
2. Build a query using only ``interests`` and scored keywords —
   deliberately **excluding** ``major`` and ``minor`` from the query
   terms.  This biases results toward adjacent domains driven by the
   user's broader intellectual curiosity.
3. If the user has very few interests/keywords beyond their major,
   use a curated list of broad interdisciplinary search terms as a
   fallback.
4. Call V2 Semantic Scholar adapter.
5. Convert to ``SpotlightCandidate`` with ``metadata["excluded_fields"]``
   set to the excluded fields for auditability.

LIMITATIONS
-----------
* Without embeddings, a field-of-study taxonomy, or an ML model, "beyond
  your field" is approximated by **keyword exclusion**.  The strategy
  simply removes ``major`` and ``minor`` from the query and relies on
  remaining interests/keywords to surface adjacent-domain research.
* A user with no interests beyond their major/minor will receive papers
  from the interdisciplinary fallback terms, which may not be highly
  personalised.
* The ``fieldsOfStudy`` / ``s2FieldsOfStudy`` metadata is included in
  candidate output for future filtering that can verify the result is
  truly outside the user's field.
"""

from __future__ import annotations

import logging
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

# Broad interdisciplinary topics used as a fallback when the user has no
# interests or keywords beyond their major/minor.  Intentionally diverse.
_INTERDISCIPLINARY_FALLBACK_TERMS = [
    "interdisciplinary research",
    "science and society",
    "data science applications",
    "sustainability",
    "cognitive science",
]


class BeyondYourFieldStrategy(SpotlightStrategy):
    """Recommend research outside the user's primary discipline."""

    spotlight_type = SpotlightType.beyond_your_field

    async def get_candidates(
        self,
        context: SpotlightUserContext,
    ) -> list[SpotlightCandidate]:
        # Fields to exclude from the query so results skew away from
        # the user's primary discipline.
        excluded_fields = ["major", "minor"]
        limit = resolve_spotlight_search_limit(context)
        papers, query = await search_spotlight_candidates(
            context.extracted_keywords,
            search_fn=semantic_scholar_adapter.search_papers_v2,
            exclude_fields=excluded_fields,
            fields_of_study=None,
            limit=limit,
            user_id=context.user_id,
            log_prefix="beyond-field",
        )

        # If excluding major/minor left us with no query, fall back to
        # broad interdisciplinary terms.
        used_fallback = False
        if not query:
            query = " ".join(_INTERDISCIPLINARY_FALLBACK_TERMS[:3])
            used_fallback = True
            logger.info(
                "[beyond-field] user=%s  using interdisciplinary fallback",
                context.user_id,
            )
            papers, query = await search_spotlight_candidates(
                context.extracted_keywords,
                search_fn=semantic_scholar_adapter.search_papers_v2,
                fields_of_study=None,
                limit=limit,
                user_id=context.user_id,
                log_prefix="beyond-field",
                override_queries=[query],
            )

        logger.info(
            "[beyond-field] user=%s query=%r fields_of_study=%s excluded=%r fallback=%s "
            "encoded_query=%r",
            context.user_id,
            query,
            None,
            excluded_fields,
            used_fallback,
            encode_spotlight_query(query) if query else "",
        )

        extra_metadata: dict[str, Any] = {
            "excluded_fields": excluded_fields,
            "used_fallback": used_fallback,
        }
        if context.major:
            extra_metadata["user_major"] = context.major
        if context.minor:
            extra_metadata["user_minor"] = context.minor

        candidates = raw_papers_to_candidates(
            papers,
            query=query,
            spotlight_type=SpotlightType.beyond_your_field,
            extra_metadata=extra_metadata,
        )

        logger.info(
            "[beyond-field] user=%s  candidates=%d",
            context.user_id,
            len(candidates),
        )
        return candidates
