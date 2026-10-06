from __future__ import annotations

from uuid import uuid4

import pytest

from apps.learningspotlight.schemas import (
    LearningSpotlightAuthor,
    SpotlightCandidate,
    SpotlightUserContext,
)
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
from apps.learningspotlight.services.spotlight_strategy import (
    SpotlightStrategy,
    get_spotlight_strategy,
)
from common.enums import SpotlightType

_STRATEGY_BY_TYPE: dict[SpotlightType, type[SpotlightStrategy]] = {
    SpotlightType.leading_thinker: LeadingThinkerStrategy,
    SpotlightType.country_perspective: CountryPerspectiveStrategy,
    SpotlightType.influential_research: InfluentialResearchStrategy,
    SpotlightType.latest_research: LatestResearchStrategy,
    SpotlightType.beyond_your_field: BeyondYourFieldStrategy,
}


@pytest.mark.parametrize("spotlight_type,strategy_cls", list(_STRATEGY_BY_TYPE.items()))
def test_each_spotlight_type_resolves_to_correct_strategy(
    spotlight_type: SpotlightType,
    strategy_cls: type[SpotlightStrategy],
) -> None:
    strategy = get_spotlight_strategy(spotlight_type)
    assert isinstance(strategy, strategy_cls)
    assert strategy.spotlight_type is spotlight_type


def test_unsupported_spotlight_type_fails_cleanly() -> None:
    with pytest.raises(ValueError, match="Unsupported spotlight type"):
        get_spotlight_strategy("not_a_spotlight_type")  # type: ignore[arg-type]


@pytest.mark.parametrize("strategy_cls", list(_STRATEGY_BY_TYPE.values()))
def test_all_strategies_implement_common_interface(
    strategy_cls: type[SpotlightStrategy],
) -> None:
    strategy = strategy_cls()
    assert isinstance(strategy, SpotlightStrategy)
    assert hasattr(strategy, "get_candidates")
    assert callable(strategy.get_candidates)
    assert strategy.spotlight_type in SpotlightType


@pytest.mark.asyncio
async def test_leading_thinker_get_candidates_executes_successfully() -> None:
    """LeadingThinkerStrategy executes candidate retrieval and author evaluation."""
    context = SpotlightUserContext(
        user_id=uuid4(),
        major="Computer Science",
        extracted_keywords={"major": ["Computer Science"]},
    )
    fake_papers = {
        "data": [
            {
                "paperId": "p_thinker_1",
                "title": "Foundation Models",
                "abstract": "We review foundation models and their applications.",
                "authors": [{"authorId": "a1", "name": "Yann LeCun"}],
                "citationCount": 500,
                "url": "https://example.com/p1",
                "openAccessPdf": {
                    "url": "https://example.com/p1.pdf",
                    "status": "GOLD",
                    "license": "CC-BY",
                },
            }
        ]
    }
    async def mock_search(query, limit=None, **kwargs):
        return fake_papers, 200

    async def mock_author_lookup(ids):
        return {"a1": {"authorId": "a1", "name": "Yann LeCun", "citationCount": 50000, "hIndex": 80, "paperCount": 300}}

    strat = LeadingThinkerStrategy(search_fn=mock_search, author_lookup_fn=mock_author_lookup)
    candidates = await strat.get_candidates(context)
    assert len(candidates) == 1
    assert candidates[0].paper_id == "p_thinker_1"
    assert candidates[0].metadata["thinker_score"] > 0



def test_candidate_model_represents_expected_paper_fields() -> None:
    candidate = SpotlightCandidate(
        paper_id="p1",
        title="Title",
        authors=[LearningSpotlightAuthor(author_id="a1", name="Ada")],
        abstract="Abs",
        venue="ICML",
        year=2025,
        citation_count=25,
        url="https://example.com/p1",
        query='("computer science")',
        spotlight_type=SpotlightType.influential_research,
        metadata={"source": "unit-test"},
    )
    dumped = candidate.model_dump()
    assert dumped["paper_id"] == "p1"
    assert dumped["citation_count"] == 25
    assert dumped["query"] == '("computer science")'
    assert dumped["spotlight_type"] == "influential_research"
    assert dumped["authors"][0]["name"] == "Ada"
    assert dumped["metadata"]["source"] == "unit-test"


def test_strategy_context_represents_user_learning_data() -> None:
    user_id = uuid4()
    context = SpotlightUserContext(
        user_id=user_id,
        country="India",
        major="Computer Science",
        minor="AI",
        interests=["NLP", "ML"],
        extracted_keywords={
            "major": ["Computer Science"],
            "content_keywords": {"transformers": 3},
        },
    )
    assert context.user_id == user_id
    assert context.country == "India"
    assert context.major == "Computer Science"
    assert context.minor == "AI"
    assert context.interests == ["NLP", "ML"]
    assert context.extracted_keywords["content_keywords"]["transformers"] == 3
