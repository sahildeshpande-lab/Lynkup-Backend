"""Tests for Step 18 — keyword normalization before Semantic Scholar."""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from apps.learningspotlight.schemas import SpotlightCandidate, SpotlightUserContext
from apps.learningspotlight.services.academic_vocabulary_repository import (
    dedupe_canonical_terms,
    load_canonical_academic_terms,
)
from apps.learningspotlight.services.beyond_your_field_strategy import (
    BeyondYourFieldStrategy,
)
from apps.learningspotlight.services.candidate_filter_service import (
    CandidateFilterService,
)
from apps.learningspotlight.services.candidate_scoring_service import (
    CandidateScoringService,
)
from apps.learningspotlight.services.country_perspective_strategy import (
    CountryPerspectiveStrategy,
)
from apps.learningspotlight.services.influential_research_strategy import (
    InfluentialResearchStrategy,
)
from apps.learningspotlight.services.keyword_normalization_service import (
    KeywordNormalizationService,
    get_keyword_normalization_service,
    normalize_extracted_keywords_for_spotlight,
    refresh_keyword_vocabulary_from_db,
    reset_keyword_normalization_service,
)
from apps.learningspotlight.services.latest_research_strategy import (
    LatestResearchStrategy,
)
from apps.learningspotlight.services.leading_thinker_strategy import (
    LeadingThinkerStrategy,
)
from apps.learningspotlight.services.query_helpers import build_spotlight_query
from common.enums import SpotlightType

_SS_MOCK_PATH = (
    "apps.learningspotlight.services.semantic_scholar_adapter.search_papers_v2"
)


def _sample_catalog_terms() -> list[str]:
    return [
        "Artificial Intelligence",
        "Machine Learning",
        "Advanced Criminal Justice",
        "Cybersecurity",
        "Data Science",
        "AI",
    ]


class _ScalarResult:
    def __init__(self, values: list[str]) -> None:
        self._values = values

    def scalars(self) -> _ScalarResult:
        return self

    def all(self) -> list[str]:
        return self._values


@pytest.fixture(autouse=True)
def _reset_normalization_service() -> None:
    reset_keyword_normalization_service()
    yield
    reset_keyword_normalization_service()


@pytest.fixture
def service() -> KeywordNormalizationService:
    svc = get_keyword_normalization_service()
    svc.refresh_vocabulary(_sample_catalog_terms())
    return svc


def test_exact_match(service: KeywordNormalizationService) -> None:
    result = service.normalize_keyword("machine learning")
    assert result.matched is True
    assert result.match_type == "exact"
    assert result.normalized == "Machine Learning"


def test_whitespace_normalization(service: KeywordNormalizationService) -> None:
    result = service.normalize_keyword("  machine   learning  ")
    assert result.normalized == "Machine Learning"


def test_case_normalization(service: KeywordNormalizationService) -> None:
    result = service.normalize_keyword("MACHINE LEARNING")
    assert result.normalized == "Machine Learning"


def test_typo_machine_learning(service: KeywordNormalizationService) -> None:
    result = service.normalize_keyword("machne learning")
    assert result.matched is True
    assert result.normalized == "Machine Learning"
    assert result.score is not None
    assert result.score >= 80


def test_typo_artificial_intelligence(service: KeywordNormalizationService) -> None:
    result = service.normalize_keyword("artifical inteligence")
    assert result.matched is True
    assert result.normalized == "Artificial Intelligence"


def test_complex_typo_advanced_criminal_justice(
    service: KeywordNormalizationService,
) -> None:
    result = service.normalize_keyword("advnace criminal justificaion")
    assert result.matched is True
    assert result.normalized == "Advanced Criminal Justice"


def test_low_confidence_keeps_original(service: KeywordNormalizationService) -> None:
    result = service.normalize_keyword("quantum basket weaving")
    assert result.matched is False
    assert result.normalized == "quantum basket weaving"


def test_ambiguous_match_keeps_original(service: KeywordNormalizationService) -> None:
    service.refresh_vocabulary(
        [
            "Data Science",
            "Data Analytics",
            "Data Analysis",
        ]
    )
    result = service.normalize_keyword("data sci")
    assert result.matched is False
    assert result.normalized == "data sci"


def test_deduplicate_normalized_keywords(service: KeywordNormalizationService) -> None:
    normalized = service.normalize_extracted_keywords(
        {
            "major": [
                "machine learning",
                "machne learning",
                "Machine Learning",
            ]
        }
    )
    assert normalized["major"] == ["Machine Learning"]


def test_empty_input_is_safe(service: KeywordNormalizationService) -> None:
    assert service.normalize_keyword("").normalized == ""
    assert service.normalize_keyword("   ").matched is False
    assert service.normalize_extracted_keywords(None) == {}


def test_short_keyword_requires_exact_or_high_confidence(
    service: KeywordNormalizationService,
) -> None:
    exact = service.normalize_keyword("AI")
    assert exact.normalized == "AI"
    assert exact.matched is True

    service.refresh_vocabulary(["Artificial Intelligence", "Machine Learning"])
    risky = service.normalize_keyword("ai")
    assert risky.matched is False
    assert risky.normalized == "ai"


def test_scored_dict_keys_are_normalized_and_merged(
    service: KeywordNormalizationService,
) -> None:
    normalized = service.normalize_extracted_keywords(
        {
            "engagement_keywords": {
                "machine learning": 2,
                "Machine Learning": 3,
                "machne learning": 1,
            }
        }
    )
    assert normalized["engagement_keywords"]["Machine Learning"] == 5
    assert normalized["engagement_keywords"]["machne learning"] == 1


def test_fuzzy_false_skips_rapidfuzz_extract(
    service: KeywordNormalizationService,
) -> None:
    with patch(
        "apps.learningspotlight.services.keyword_normalization_service.process.extract"
    ) as extract:
        result = service.normalize_keyword("unmatched engagement term", fuzzy=False)
        extract.assert_not_called()
    assert result.matched is False
    assert result.normalized == "unmatched engagement term"


def test_dedupe_canonical_terms_preserves_first_casing() -> None:
    terms = dedupe_canonical_terms(
        ["Machine Learning", "machine learning", "Data Science", "  Data Science  "]
    )
    assert terms == ["Machine Learning", "Data Science"]


@pytest.mark.asyncio
async def test_load_canonical_academic_terms_from_repository() -> None:
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[
            _ScalarResult(["Machine Learning"]),
            _ScalarResult(["Mathematics"]),
            _ScalarResult(["Robotics"]),
        ]
    )
    terms = await load_canonical_academic_terms(session)
    assert terms == ["Machine Learning", "Mathematics", "Robotics"]
    assert session.execute.await_count == 3


@pytest.mark.asyncio
async def test_vocabulary_cache_avoids_repeated_db_load() -> None:
    service = get_keyword_normalization_service()
    session = AsyncMock()
    with patch(
        "apps.learningspotlight.services.keyword_normalization_service.load_canonical_academic_terms",
        AsyncMock(return_value=_sample_catalog_terms()),
    ) as loader:
        await service.ensure_loaded(session)
        await service.ensure_loaded(session)
        service.normalize_keyword("machne learning")
        loader.assert_awaited_once()


def test_refresh_replaces_cached_vocabulary(service: KeywordNormalizationService) -> None:
    assert service.normalize_keyword("machne learning").normalized == "Machine Learning"
    service.refresh_vocabulary(["Cybersecurity"])
    assert service.normalize_keyword("machne learning").normalized == "machne learning"
    assert service.normalize_keyword("cybersecurity").normalized == "Cybersecurity"


def test_build_spotlight_query_uses_normalized_terms(
    service: KeywordNormalizationService,
) -> None:
    query = build_spotlight_query(
        {
            "major": ["machne learning"],
            "interests": ["artifical inteligence"],
        }
    )
    lower = query.lower()
    assert "machine learning" in lower
    assert "artificial intelligence" in lower


def test_backward_compatible_extracted_keywords_shape(
    service: KeywordNormalizationService,
) -> None:
    raw = {
        "major": ["Computer Science"],
        "minor": ["Mathematics"],
        "interests": ["NLP"],
        "engagement_keywords": {"deep learning": 4},
        "content_keywords": {"neural networks": 2},
        "hashtags": {"ai": 1},
    }
    normalized = service.normalize_extracted_keywords(raw)
    assert isinstance(normalized["major"], list)
    assert isinstance(normalized["engagement_keywords"], dict)
    assert set(normalized.keys()) == set(raw.keys())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "strategy_cls",
    [
        LeadingThinkerStrategy,
        InfluentialResearchStrategy,
        LatestResearchStrategy,
        BeyondYourFieldStrategy,
    ],
)
async def test_all_strategies_receive_normalized_semantic_scholar_query(
    service: KeywordNormalizationService,
    strategy_cls: type,
) -> None:
    context = SpotlightUserContext(
        user_id=uuid4(),
        country="India",
        major="machne learning",
        minor="Mathematics",
        interests=["artifical inteligence"],
        extracted_keywords={
            "major": ["machne learning"],
            "minor": ["Mathematics"],
            "interests": ["artifical inteligence"],
        },
    )
    strategy_kwargs = {}
    if strategy_cls is LeadingThinkerStrategy:
        strategy_kwargs["author_lookup_fn"] = AsyncMock(return_value={})
    with patch(_SS_MOCK_PATH, AsyncMock(return_value=({"data": [], "total": 0}, 200))) as search:
        strategy = strategy_cls(**strategy_kwargs)
        await strategy.get_candidates(context)
        assert search.called
        query = search.await_args.args[0]
        lower = query.lower()
        assert "machine learning" in lower or "artificial intelligence" in lower


@pytest.mark.asyncio
async def test_country_perspective_preserves_raw_profile_phrases(
    service: KeywordNormalizationService,
) -> None:
    """Country Perspective must not fuzzy-normalize or split profile phrases."""
    context = SpotlightUserContext(
        user_id=uuid4(),
        country="Ghana",
        major="Information Studies",
        minor=None,
        interests=["Political Science and Government", "Brand Designer"],
        extracted_keywords={
            "major": ["Information Studies"],
            "interests": [
                "Political Science and Government",
                "Brand Designer",
            ],
        },
    )
    with patch(_SS_MOCK_PATH, AsyncMock(return_value=({"data": [], "total": 0}, 200))) as search:
        await CountryPerspectiveStrategy().get_candidates(context)
        assert search.called
        query = search.await_args.args[0]
        assert query == (
            '("Information Studies"+Ghana)|'
            '("Political Science and Government"+Ghana)|'
            '("Brand Designer"+Ghana)'
        )
        kwargs = search.await_args.kwargs
        assert kwargs.get("fields_of_study") is None


@pytest.mark.asyncio
async def test_beyond_your_field_excludes_normalized_major_from_query(
    service: KeywordNormalizationService,
) -> None:
    context = SpotlightUserContext(
        user_id=uuid4(),
        major="machne learning",
        minor="Mathematics",
        extracted_keywords={
            "major": ["machne learning"],
            "minor": ["Mathematics"],
            "interests": ["artifical inteligence"],
        },
    )
    strategy = BeyondYourFieldStrategy()
    with patch(_SS_MOCK_PATH, AsyncMock(return_value=({"data": [], "total": 0}, 200))) as search:
        await strategy.get_candidates(context)
        query = search.await_args.args[0].lower()
        assert "machine learning" not in query
        assert "mathematics" not in query
        assert "artificial intelligence" in query


def test_hyphen_and_punctuation_normalize_to_canonical(
    service: KeywordNormalizationService,
) -> None:
    result = service.normalize_keyword("Artificial-Intelligence")
    assert result.matched is True
    assert result.normalized == "Artificial Intelligence"


def test_large_length_difference_keeps_original(
    service: KeywordNormalizationService,
) -> None:
    result = service.normalize_keyword("data")
    assert result.matched is False
    assert result.normalized == "data"


@pytest.mark.asyncio
async def test_refresh_from_db_replaces_cache() -> None:
    service = get_keyword_normalization_service()
    service.refresh_vocabulary(["Machine Learning"])
    session = AsyncMock()
    with patch(
        "apps.learningspotlight.services.keyword_normalization_service.load_canonical_academic_terms",
        AsyncMock(return_value=["Cybersecurity"]),
    ) as loader:
        await refresh_keyword_vocabulary_from_db(session)
        loader.assert_awaited_once_with(session)
    assert service.normalize_keyword("cybersecurity").normalized == "Cybersecurity"
    assert service.normalize_keyword("machne learning").normalized == "machne learning"


def test_scoring_uses_normalized_keywords(
    service: KeywordNormalizationService,
) -> None:
    context = SpotlightUserContext(
        user_id=uuid4(),
        major="machne learning",
        extracted_keywords={"major": ["machne learning"]},
    )
    candidate = SpotlightCandidate(
        paper_id="p-ml",
        title="Advances in Machine Learning",
        abstract="A survey of machine learning methods.",
        url="https://example.com/ml",
        query="Machine Learning",
        spotlight_type=SpotlightType.influential_research,
        citation_count=40,
        year=2025,
    )
    typo_score = CandidateScoringService().calculate_user_relevance(candidate, context)
    canonical_context = SpotlightUserContext(
        user_id=context.user_id,
        major="Machine Learning",
        extracted_keywords={"major": ["Machine Learning"]},
    )
    canonical_score = CandidateScoringService().calculate_user_relevance(
        candidate, canonical_context
    )
    assert typo_score == canonical_score
    assert typo_score > 0


def test_beyond_your_field_filter_uses_normalized_major(
    service: KeywordNormalizationService,
) -> None:
    context = SpotlightUserContext(
        user_id=uuid4(),
        major="machne learning",
        extracted_keywords={"major": ["machne learning"]},
    )
    in_field = SpotlightCandidate(
        paper_id="p-ml",
        title="Machine Learning Survey",
        abstract="A survey of machine learning.",
        url="https://example.com/ml",
        query="Artificial Intelligence",
        spotlight_type=SpotlightType.beyond_your_field,
        metadata={"fields_of_study": ["Machine Learning"]},
    )
    outside = SpotlightCandidate(
        paper_id="p-bio",
        title="Plant Biology",
        abstract="A study of plants.",
        url="https://example.com/bio",
        query="Artificial Intelligence",
        spotlight_type=SpotlightType.beyond_your_field,
        metadata={"fields_of_study": ["Biology"]},
    )
    result = CandidateFilterService().filter_candidates(
        [in_field, outside],
        context=context,
    )
    assert [c.paper_id for c in result.candidates] == ["p-bio"]


def test_no_hardcoded_academic_vocabulary_in_source() -> None:
    from pathlib import Path

    source = Path("apps/learningspotlight/services/keyword_normalization_service.py").read_text(
        encoding="utf-8"
    )
    assert "ACADEMIC_TERMS" not in source
    assert "CANONICAL_TERMS" not in source
    assert "Machine Learning" not in source
    repo = Path("apps/learningspotlight/services/academic_vocabulary_repository.py").read_text(
        encoding="utf-8"
    )
    assert "Machine Learning" not in repo
    assert "AcademicInterest" in repo
    assert "Major" in repo
    assert "Minor" in repo


_LEGAL_EXAMPLE_KEYWORDS = [
    "Banking, Corporate, Finance, and Securities Law",
    "American/US /Legal Studies/Jurisprudence",
    "Applied Constitutional Law in American/US Law/Legal Studies/Jurisprudenc2",
    "Advanced Constitutional Law in American/US Law/Legal Studies/Jurisprudence",
    "Advanced Criminal Law imerican/US Law/Legal Studies/Jurisprudence",
]


def _term_set(prepared) -> set[str]:
    return {term.casefold() for term in prepared.all_terms()}


def test_legal_comma_list_derives_useful_components(
    service: KeywordNormalizationService,
) -> None:
    prepared = service.prepare_search_terms(
        ["Banking, Corporate, Finance, and Securities Law"]
    )
    terms = _term_set(prepared)
    assert "banking" in terms
    assert "corporate" in terms
    assert "finance" in terms
    assert "securities law" in terms
    assert "securitieslaw" not in terms
    assert "law" not in terms


def test_constitutional_hierarchy_derives_useful_phrases(
    service: KeywordNormalizationService,
) -> None:
    prepared = service.prepare_search_terms(
        [
            "Advanced Constitutional Law in American/US Law/Legal Studies/Jurisprudence"
        ]
    )
    terms = _term_set(prepared)
    assert "constitutional law" in terms
    assert "jurisprudence" in terms
    assert "law" not in terms


def test_criminal_hierarchy_derives_useful_phrases(
    service: KeywordNormalizationService,
) -> None:
    prepared = service.prepare_search_terms(
        ["Advanced Criminal Law imerican/US Law/Legal Studies/Jurisprudence"]
    )
    terms = _term_set(prepared)
    assert "criminal law" in terms
    assert "jurisprudence" in terms


def test_technical_comma_list_derives_domain_phrases(
    service: KeywordNormalizationService,
) -> None:
    prepared = service.prepare_search_terms(
        ["Machine Learning, Artificial Intelligence, Data Science"]
    )
    terms = _term_set(prepared)
    assert "machine learning" in terms
    assert "artificial intelligence" in terms
    assert "data science" in terms
    assert "machinelearning" not in terms
    assert "artificialintelligence" not in terms
    assert "datascience" not in terms


def test_normal_phrase_is_preserved(service: KeywordNormalizationService) -> None:
    prepared = service.prepare_search_terms(["machine learning"])
    assert prepared.stage1_terms == ("Machine Learning",)
    assert "machinelearning" not in _term_set(prepared)


def test_spaces_are_preserved_in_search_terms(
    service: KeywordNormalizationService,
) -> None:
    prepared = service.prepare_search_terms(["machine learning"])
    for term in prepared.all_terms():
        assert " " in term
        assert term.casefold() == "machine learning"


def test_extract_topic_concepts_from_verbose_labels() -> None:
    from apps.learningspotlight.services.keyword_normalization_service import (
        extract_topic_concepts,
    )

    acting = {c.casefold() for c in extract_topic_concepts(
        "Applied Critical Thinking In Acting"
    )}
    assert "critical thinking" in acting
    assert "acting" in acting
    assert "applied critical thinking in acting" not in acting

    drama = {c.casefold() for c in extract_topic_concepts(
        "Drama/Theatre Arts and Stagecraft"
    )}
    assert "drama" in drama
    assert "theatre" in drama
    assert "stagecraft" in drama

    visual = {c.casefold() for c in extract_topic_concepts(
        "advanced visual composition"
    )}
    assert "visual composition" in visual
    assert "advanced visual composition" not in visual


def test_extract_topic_concepts_works_for_arbitrary_interests() -> None:
    from apps.learningspotlight.services.keyword_normalization_service import (
        extract_topic_concepts,
    )

    ml = {c.casefold() for c in extract_topic_concepts(
        "Applied Machine Learning in Healthcare"
    )}
    assert "machine learning" in ml
    assert "healthcare" in ml

    story = {c.casefold() for c in extract_topic_concepts(
        "Digital Storytelling and Media"
    )}
    assert "digital storytelling" in story or "storytelling" in story
    assert "media" in story

    urban = {c.casefold() for c in extract_topic_concepts("Sustainable Urban Design")}
    assert urban
    assert "sustainable urban design" in urban or "urban design" in urban

    econ = {c.casefold() for c in extract_topic_concepts("Behavioral Economics")}
    assert "behavioral economics" in econ
    from urllib.parse import quote_plus

    from apps.learningspotlight.services.query_helpers import encode_spotlight_query

    query = "(" + "|".join(f'"{term}"' for term in ["machine learning", "data science"]) + ")"
    encoded = encode_spotlight_query(query)
    assert encoded == quote_plus(query)
    assert "machine+learning" in encoded
    assert "data+science" in encoded
    assert "machinelearning" not in encoded
    assert "datascience" not in encoded


def test_duplicate_keyword_variants_are_deduped(
    service: KeywordNormalizationService,
) -> None:
    prepared = service.prepare_search_terms(
        ["machine learning", "Machine Learning", "machine  learning"]
    )
    lowered = [term.casefold() for term in prepared.stage1_terms]
    assert lowered.count("machine learning") == 1


def test_empty_values_are_handled_safely(service: KeywordNormalizationService) -> None:
    prepared = service.prepare_search_terms(["", "   ", None])  # type: ignore[list-item]
    assert prepared.raw_keywords == ()
    assert prepared.stage1_terms == ()
    assert prepared.stage3_terms == ()
    assert build_spotlight_query({"interests": ["", None, "  "]}) == ""


def test_very_long_labels_do_not_explode_query_terms(
    service: KeywordNormalizationService,
) -> None:
    from apps.learningspotlight.services.keyword_normalization_service import (
        MAX_TERMS_PER_STAGE,
    )

    long_label = "/".join([f"Segment Label {index} Studies" for index in range(40)])
    prepared = service.prepare_search_terms([long_label])
    assert len(prepared.stage1_terms) <= MAX_TERMS_PER_STAGE
    assert len(prepared.stage2_terms) <= MAX_TERMS_PER_STAGE
    assert len(prepared.stage3_terms) <= MAX_TERMS_PER_STAGE
    assert len(prepared.all_terms()) <= MAX_TERMS_PER_STAGE


def test_no_manual_taxonomy_or_noise_word_lists() -> None:
    from pathlib import Path

    spotlight_root = Path("apps/learningspotlight")
    banned = (
        "ACADEMIC_TERMS",
        "CANONICAL_TERMS",
        "NOISE_WORDS",
        "LEGAL_TERMS",
    )
    hardcoded_examples = (
        "constitutional law",
        "securities law",
        "jurisprudence",
        "criminal law",
    )
    for path in spotlight_root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for token in banned:
            assert token not in source, f"{token} found in {path}"
        lower = source.lower()
        for example in hardcoded_examples:
            assert (
                f'"{example}"' not in lower
                and f"'{example}'" not in lower
            ), f"hard-coded academic example {example!r} found in {path}"


def test_legal_example_builds_spaced_boolean_query(
    service: KeywordNormalizationService,
) -> None:
    query = build_spotlight_query({"interests": _LEGAL_EXAMPLE_KEYWORDS})
    assert query
    lower = query.lower()
    assert "securities law" in lower or "banking" in lower
    assert "securitieslaw" not in lower
    assert "|" in query


@pytest.mark.asyncio
async def test_broader_structural_query_used_only_when_needed() -> None:
    from apps.learningspotlight.services.query_helpers import (
        MAX_QUERY_ATTEMPTS,
        search_spotlight_candidates,
    )

    empty = ({"data": [], "total": 0}, 200)
    filled = ({"data": [{"paperId": "p1", "abstract": "abs"}], "total": 1}, 200)
    calls: list[str] = []

    async def mock_search(query, **kwargs):
        calls.append(query)
        if len(calls) == 1:
            return empty
        return filled

    keywords = {"interests": _LEGAL_EXAMPLE_KEYWORDS}
    papers, used_query = await search_spotlight_candidates(
        keywords,
        search_fn=mock_search,
        limit=30,
    )
    assert papers
    assert used_query == calls[-1]
    assert 1 < len(calls) <= MAX_QUERY_ATTEMPTS
    assert calls[0] != calls[-1]


@pytest.mark.asyncio
async def test_simple_phrase_does_not_trigger_fallback() -> None:
    from apps.learningspotlight.services.query_helpers import search_spotlight_candidates

    calls: list[str] = []

    async def mock_search(query, **kwargs):
        calls.append(query)
        return ({"data": [{"paperId": "p1"}], "total": 1}, 200)

    papers, _used = await search_spotlight_candidates(
        {"major": ["machine learning"]},
        search_fn=mock_search,
        limit=30,
    )
    assert papers
    assert len(calls) == 1


def test_all_five_strategies_use_shared_query_preparation() -> None:
    from pathlib import Path

    strategy_files = [
        "leading_thinker_strategy.py",
        "country_perspective_strategy.py",
        "influential_research_strategy.py",
        "latest_research_strategy.py",
        "beyond_your_field_strategy.py",
    ]
    for filename in strategy_files:
        source = Path("apps/learningspotlight/services", filename).read_text(
            encoding="utf-8"
        )
        assert "search_spotlight_candidates" in source
        assert "NOISE_WORDS" not in source
        assert "ACADEMIC_TERMS" not in source


def test_raw_extracted_keywords_are_not_rewritten_by_query_builder() -> None:
    raw = {
        "interests": list(_LEGAL_EXAMPLE_KEYWORDS),
        "major": ["Computer Science"],
    }
    snapshot = {
        "interests": list(raw["interests"]),
        "major": list(raw["major"]),
    }
    build_spotlight_query(raw)
    assert raw == snapshot


def test_beyond_your_field_does_not_treat_derived_terms_as_major(
    service: KeywordNormalizationService,
) -> None:
    context = SpotlightUserContext(
        user_id=uuid4(),
        major="Advanced Constitutional Law in American/US Law/Legal Studies/Jurisprudence",
        extracted_keywords={
            "major": [
                "Advanced Constitutional Law in American/US Law/Legal Studies/Jurisprudence"
            ],
            "interests": ["climate policy"],
        },
    )
    in_field = SpotlightCandidate(
        paper_id="p-const",
        title="Constitutional Law Review",
        abstract="A paper about constitutional law.",
        url="https://example.com/const",
        query="climate policy",
        spotlight_type=SpotlightType.beyond_your_field,
        metadata={"fields_of_study": ["Constitutional Law"]},
    )
    result = CandidateFilterService().filter_candidates([in_field], context=context)
    # Derived "constitutional law" is a search fragment, not the user's major.
    assert [c.paper_id for c in result.candidates] == ["p-const"]


def test_query_term_cap_defaults_to_eight(service: KeywordNormalizationService) -> None:
    from apps.learningspotlight.config import LearningSpotlightSettings
    from apps.learningspotlight.services.keyword_normalization_service import (
        refine_query_terms,
    )
    from apps.learningspotlight.services.query_helpers import build_spotlight_query_plan

    cfg = LearningSpotlightSettings()
    assert cfg.learning_spotlight_max_query_terms == 8
    assert (
        LearningSpotlightSettings.model_fields["learning_spotlight_candidate_limit"].default
        == 50
    )
    assert cfg.learning_spotlight_batch_size == 50

    many = [f"Distinct Phrase {index} Studies" for index in range(20)]
    refined = refine_query_terms(many)
    assert len(refined) <= 8

    plan = build_spotlight_query_plan({"interests": many})
    for terms in plan.stage_term_lists:
        assert len(terms) <= 8
    query = build_spotlight_query({"interests": many})
    quoted = re.findall(r'"([^"]+)"', query)
    assert len(quoted) <= 8


def test_overlapping_unigrams_are_removed_from_query_terms(
    service: KeywordNormalizationService,
) -> None:
    from apps.learningspotlight.services.keyword_normalization_service import (
        refine_query_terms,
    )

    refined = refine_query_terms(
        [
            "applied engineering",
            "engineering",
            "engineering design",
            "design",
            "operations technology",
            "technology",
            "space",
            "operations",
            "applied",
        ]
    )
    lowered = {term.casefold() for term in refined}
    assert "applied engineering" in lowered
    assert "engineering design" in lowered
    assert "operations technology" in lowered
    assert "engineering" not in lowered
    assert "design" not in lowered
    assert "technology" not in lowered
    assert "operations" not in lowered
    assert "applied" not in lowered
    assert len(refined) <= 8


def test_slash_path_query_does_not_keep_covered_unigrams(
    service: KeywordNormalizationService,
) -> None:
    query = build_spotlight_query(
        {
            "interests": [
                "Aircraft Armament Systems Technology / operations technology / "
                "engineering design / space operations / design for / applied engineering"
            ]
        }
    )
    quoted = [term.casefold() for term in re.findall(r'"([^"]+)"', query)]
    assert len(quoted) <= 8
    covered_unigrams = {
        "engineering",
        "design",
        "space",
        "operations",
        "technology",
        "applied",
    }
    assert covered_unigrams.isdisjoint(quoted)


@pytest.mark.asyncio
async def test_too_many_hits_narrows_and_does_not_broaden() -> None:
    import re as _re

    from apps.learningspotlight.services.query_helpers import search_spotlight_candidates

    calls: list[str] = []

    async def mock_search(query, **kwargs):
        calls.append(query)
        terms = _re.findall(r'"([^"]+)"', query)
        if len(terms) > 2:
            return (
                {
                    "data": [],
                    "total": 0,
                    "error": 'Search returned too many hits (33457472 of 10000000) Refine or consider using the datasets API',
                },
                400,
            )
        return ({"data": [{"paperId": "p1", "abstract": "abs"}], "total": 1}, 200)

    papers, used_query = await search_spotlight_candidates(
        None,
        search_fn=mock_search,
        override_queries=['("alpha"|"bravo"|"charlie"|"delta")'],
        limit=30,
    )
    assert papers
    assert used_query == calls[-1]
    first_count = len(_re.findall(r'"([^"]+)"', calls[0]))
    last_count = len(_re.findall(r'"([^"]+)"', calls[-1]))
    assert last_count < first_count
    assert last_count <= 2
    for query in calls[1:]:
        assert len(_re.findall(r'"([^"]+)"', query)) <= first_count

