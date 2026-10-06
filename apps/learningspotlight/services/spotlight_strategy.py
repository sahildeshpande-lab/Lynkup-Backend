"""Learning Spotlight strategy interface and type → strategy resolver.

Strategies produce intermediate ``SpotlightCandidate`` lists. They do not call
Semantic Scholar in this step — subclasses raise ``NotImplementedError`` until
category-specific query logic is implemented.

Future daily generation wiring (Step 4 orchestration):

    strategy = get_spotlight_strategy(spotlight_type)
    candidates = await strategy.get_candidates(context)
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from apps.learningspotlight.config import settings as spotlight_settings
from apps.learningspotlight.schemas import SpotlightCandidate, SpotlightUserContext
from common.enums import SpotlightType


def resolve_spotlight_search_limit(context: SpotlightUserContext) -> int:
    """Semantic Scholar ``limit`` for one strategy fetch."""
    if context.search_limit is not None and context.search_limit > 0:
        return context.search_limit
    return spotlight_settings.learning_spotlight_candidate_limit


class SpotlightStrategy(ABC):
    """Common interface for the five Learning Spotlight recommendation strategies."""

    spotlight_type: SpotlightType

    @abstractmethod
    async def get_candidates(
        self,
        context: SpotlightUserContext,
    ) -> list[SpotlightCandidate]:
        """Return intermediate paper candidates for this strategy.

        Not implemented for any category yet — later steps will call V1
        ``search_papers`` / query builders without duplicating HTTP clients.
        """


def get_spotlight_strategy(spotlight_type: SpotlightType) -> SpotlightStrategy:
    """Resolve a ``SpotlightType`` to its strategy instance."""
    from apps.learningspotlight.services.beyond_your_field_strategy import (
        BeyondYourFieldStrategy,
    )
    from apps.learningspotlight.services.country_perspective_strategy import (
        CountryPerspectiveStrategy,
    )
    from apps.learningspotlight.services.influential_research_strategy import (
        InfluentialResearchStrategy,
    )
    from apps.learningspotlight.services.latest_research_strategy import (
        LatestResearchStrategy,
    )
    from apps.learningspotlight.services.leading_thinker_strategy import (
        LeadingThinkerStrategy,
    )

    mapping: dict[SpotlightType, type[SpotlightStrategy]] = {
        SpotlightType.leading_thinker: LeadingThinkerStrategy,
        SpotlightType.country_perspective: CountryPerspectiveStrategy,
        SpotlightType.influential_research: InfluentialResearchStrategy,
        SpotlightType.latest_research: LatestResearchStrategy,
        SpotlightType.beyond_your_field: BeyondYourFieldStrategy,
    }
    strategy_cls = mapping.get(spotlight_type)
    if strategy_cls is None:
        raise ValueError(f"Unsupported spotlight type: {spotlight_type!r}")
    return strategy_cls()
