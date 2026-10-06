"""Unit tests for Step 7: Candidate Filtering Service & Candidate Limit Configuration.

Tests all filtering rules:
1. Default candidate limit = 30
2. Environment override works
3. Invalid candidate-limit configuration is rejected
4. Duplicate papers are removed
5. Missing paper_id is removed
6. Missing title is removed
7. Missing URL is removed
7b. Missing abstract is removed
8. Previously shown paper is removed
9. Previously read paper is removed
10. Previously saved paper is removed
11. Influential paper with citation_count >= 10 is retained
12. Influential paper below 10 is removed
13. Latest research outside the configured time window is removed
14. Empty final candidate set is handled safely
15. Filtering does not perform scoring/ranking
16. Leading Thinker remains unimplemented
17. Database history extraction for V2 spotlights works and ignores V1 logs
"""

from __future__ import annotations

import os
from datetime import date
from unittest.mock import patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from apps.learningspotlight.config import LearningSpotlightSettings
from apps.learningspotlight.schemas import (
    CandidateFilterResult,
    LearningSpotlightAuthor,
    SpotlightCandidate,
    SpotlightUserContext,
)
from apps.learningspotlight.services.candidate_filter_service import (
    CandidateFilterService,
    _normalize_paper_id,
    get_user_spotlight_history,
)
from apps.learningspotlight.services.candidate_scoring_service import (
    CandidateScoringService,
)
from apps.learningspotlight.services.leading_thinker_strategy import (
    LeadingThinkerStrategy,
)
from common.enums import SpotlightType


# ---------------------------------------------------------------------------
# Fixtures & Helpers
# ---------------------------------------------------------------------------


def _make_candidate(
    paper_id: str = "p1",
    title: str = "Valid Title",
    url: str = "https://example.com/p1",
    citation_count: int | None = 25,
    year: int | None = 2026,
    spotlight_type: SpotlightType = SpotlightType.influential_research,
    query: str = '("AI")',
    abstract: str | None = "Sample abstract",
    **metadata,
) -> SpotlightCandidate:
    return SpotlightCandidate(
        paper_id=paper_id,
        title=title,
        authors=[LearningSpotlightAuthor(author_id="a1", name="Author")],
        abstract=abstract,
        venue="Conference",
        year=year,
        citation_count=citation_count,
        url=url,
        query=query,
        spotlight_type=spotlight_type,
        metadata=metadata,
    )


@pytest.fixture
def filter_service() -> CandidateFilterService:
    with patch(
        "apps.learningspotlight.services.candidate_filter_service.LanguageDetectionService.is_english",
        return_value=True,
    ):
        yield CandidateFilterService()


# ---------------------------------------------------------------------------
# 1-3. Configuration tests (Candidate Limit)
# ---------------------------------------------------------------------------


def test_default_candidate_limit_is_50() -> None:
    """1. Default candidate limit must be 50 (model default, not .env override)."""
    default = LearningSpotlightSettings.model_fields[
        "learning_spotlight_candidate_limit"
    ].default
    assert default == 50


def test_candidate_limit_environment_override() -> None:
    """2. Environment override (e.g. 50) works dynamically."""
    with patch.dict(os.environ, {"LEARNING_SPOTLIGHT_CANDIDATE_LIMIT": "50"}):
        cfg = LearningSpotlightSettings()
        assert cfg.learning_spotlight_candidate_limit == 50


@pytest.mark.parametrize("invalid_val", ["0", "-5", "-100"])
def test_invalid_candidate_limit_is_rejected(invalid_val: str) -> None:
    """3. Non-positive candidate limit is rejected with ValidationError."""
    with patch.dict(os.environ, {"LEARNING_SPOTLIGHT_CANDIDATE_LIMIT": invalid_val}):
        with pytest.raises(ValidationError):
            LearningSpotlightSettings()


# ---------------------------------------------------------------------------
# 4. Duplicate removal
# ---------------------------------------------------------------------------


def test_duplicate_papers_are_removed(filter_service: CandidateFilterService) -> None:
    """4. Duplicate papers with the same paper_id are deduplicated to 1."""
    candidates = [
        _make_candidate(paper_id="p1", title="Paper 1"),
        _make_candidate(paper_id="p1", title="Paper 1 Dup"),
        _make_candidate(paper_id=" p1 ", title="Paper 1 Whitespace Dup"),
        _make_candidate(paper_id="p2", title="Paper 2"),
    ]
    result = filter_service.filter_candidates(candidates)

    assert result.original_count == 4
    assert result.duplicate_count == 2
    assert result.final_count == 2
    assert [c.paper_id for c in result.candidates] == ["p1", "p2"]


# ---------------------------------------------------------------------------
# 5-7. Missing required fields
# ---------------------------------------------------------------------------


def test_missing_paper_id_is_removed(filter_service: CandidateFilterService) -> None:
    """5. Candidate with missing/empty paper_id is removed as invalid."""
    candidates = [
        _make_candidate(paper_id="", title="Paper No ID"),
        _make_candidate(paper_id="   ", title="Paper Whitespace ID"),
        _make_candidate(paper_id="valid_1", title="Valid Paper"),
    ]
    result = filter_service.filter_candidates(candidates)

    assert result.original_count == 3
    assert result.invalid_count == 2
    assert result.final_count == 1
    assert result.candidates[0].paper_id == "valid_1"


def test_missing_title_is_removed(filter_service: CandidateFilterService) -> None:
    """6. Candidate with missing/empty title is removed as invalid."""
    candidates = [
        _make_candidate(paper_id="p1", title=""),
        _make_candidate(paper_id="p2", title="   "),
        _make_candidate(paper_id="p3", title=None),  # type: ignore[arg-type]
        _make_candidate(paper_id="p4", title="Valid Title"),
    ]
    result = filter_service.filter_candidates(candidates)

    assert result.original_count == 4
    assert result.invalid_count == 3
    assert result.final_count == 1
    assert result.candidates[0].paper_id == "p4"


def test_missing_url_is_removed(filter_service: CandidateFilterService) -> None:
    """7. Candidate with missing/empty url is removed as invalid."""
    candidates = [
        _make_candidate(paper_id="p1", url=""),
        _make_candidate(paper_id="p2", url="   "),
        _make_candidate(paper_id="p3", url=None),  # type: ignore[arg-type]
        _make_candidate(paper_id="p4", url="https://example.com/p4"),
    ]
    result = filter_service.filter_candidates(candidates)

    assert result.original_count == 4
    assert result.invalid_count == 3
    assert result.final_count == 1
    assert result.candidates[0].paper_id == "p4"


def test_missing_abstract_is_removed(filter_service: CandidateFilterService) -> None:
    """7b. Candidate with missing/empty abstract is removed as invalid."""
    candidates = [
        _make_candidate(paper_id="p1", abstract=""),
        _make_candidate(paper_id="p2", abstract="   "),
        _make_candidate(paper_id="p3", abstract=None),  # type: ignore[arg-type]
        _make_candidate(paper_id="p4", abstract="This paper presents a valid English abstract for testing."),
    ]
    result = filter_service.filter_candidates(candidates)

    assert result.original_count == 4
    assert result.invalid_count == 3
    assert result.final_count == 1
    assert result.candidates[0].paper_id == "p4"


# ---------------------------------------------------------------------------
# 8-10. Previously shown, read, saved
# ---------------------------------------------------------------------------


def test_previously_shown_paper_is_removed(filter_service: CandidateFilterService) -> None:
    """8. Papers previously shown to the user are removed."""
    candidates = [
        _make_candidate(paper_id="shown_1"),
        _make_candidate(paper_id="shown_2"),
        _make_candidate(paper_id="fresh_1"),
    ]
    result = filter_service.filter_candidates(
        candidates,
        previously_shown_ids={"shown_1", "shown_2"},
    )

    assert result.original_count == 3
    assert result.previously_shown_count == 2
    assert result.final_count == 1
    assert result.candidates[0].paper_id == "fresh_1"


def test_previously_read_paper_is_removed(filter_service: CandidateFilterService) -> None:
    """9. Papers previously read by the user are removed."""
    candidates = [
        _make_candidate(paper_id="read_1"),
        _make_candidate(paper_id="fresh_1"),
    ]
    result = filter_service.filter_candidates(
        candidates,
        already_read_ids={"read_1"},
    )

    assert result.original_count == 2
    assert result.previously_read_count == 1
    assert result.final_count == 1
    assert result.candidates[0].paper_id == "fresh_1"


def test_previously_saved_paper_is_removed(filter_service: CandidateFilterService) -> None:
    """10. Papers previously saved by the user are removed."""
    candidates = [
        _make_candidate(paper_id="saved_1"),
        _make_candidate(paper_id="fresh_1"),
    ]
    result = filter_service.filter_candidates(
        candidates,
        already_saved_ids={"saved_1"},
    )

    assert result.original_count == 2
    assert result.previously_saved_count == 1
    assert result.final_count == 1
    assert result.candidates[0].paper_id == "fresh_1"


# ---------------------------------------------------------------------------
# 11-12. Influential Research category validation
# ---------------------------------------------------------------------------


def test_influential_paper_with_high_citations_retained(
    filter_service: CandidateFilterService,
) -> None:
    """11. Influential paper with citation_count >= 10 is retained."""
    candidates = [
        _make_candidate(
            paper_id="p1",
            citation_count=10,
            spotlight_type=SpotlightType.influential_research,
        ),
        _make_candidate(
            paper_id="p2",
            citation_count=150,
            spotlight_type=SpotlightType.influential_research,
        ),
    ]
    result = filter_service.filter_candidates(candidates)

    assert result.original_count == 2
    assert result.category_filtered_count == 0
    assert result.final_count == 2


def test_influential_paper_below_threshold_removed(
    filter_service: CandidateFilterService,
) -> None:
    """12. Influential paper with citation_count < 10 or None is removed."""
    candidates = [
        _make_candidate(
            paper_id="p_low",
            citation_count=9,
            spotlight_type=SpotlightType.influential_research,
        ),
        _make_candidate(
            paper_id="p_zero",
            citation_count=0,
            spotlight_type=SpotlightType.influential_research,
        ),
        _make_candidate(
            paper_id="p_none",
            citation_count=None,
            spotlight_type=SpotlightType.influential_research,
        ),
        _make_candidate(
            paper_id="p_valid",
            citation_count=10,
            spotlight_type=SpotlightType.influential_research,
        ),
    ]
    result = filter_service.filter_candidates(candidates)

    assert result.original_count == 4
    assert result.category_filtered_count == 3
    assert result.final_count == 1
    assert result.candidates[0].paper_id == "p_valid"


# ---------------------------------------------------------------------------
# 13. Latest Research category validation
# ---------------------------------------------------------------------------


def test_latest_research_outside_time_window_removed(
    filter_service: CandidateFilterService,
) -> None:
    """13. Latest research older than the dynamic 1-2 year window is removed."""
    ref_date = date(2026, 8, 23)  # min_year = 2026 - 1 = 2025
    candidates = [
        _make_candidate(
            paper_id="p_recent",
            year=2026,
            spotlight_type=SpotlightType.latest_research,
        ),
        _make_candidate(
            paper_id="p_last_year",
            year=2025,
            spotlight_type=SpotlightType.latest_research,
        ),
        _make_candidate(
            paper_id="p_old",
            year=2024,
            spotlight_type=SpotlightType.latest_research,
        ),
        _make_candidate(
            paper_id="p_ancient",
            year=2015,
            spotlight_type=SpotlightType.latest_research,
        ),
        _make_candidate(
            paper_id="p_no_year",
            year=None,
            spotlight_type=SpotlightType.latest_research,
        ),
    ]
    result = filter_service.filter_candidates(
        candidates,
        reference_date=ref_date,
        max_age_years=1,
    )

    assert result.original_count == 5
    assert result.category_filtered_count == 3
    assert result.final_count == 2
    assert {c.paper_id for c in result.candidates} == {"p_recent", "p_last_year"}


# ---------------------------------------------------------------------------
# 14. Empty / Zero candidate safety
# ---------------------------------------------------------------------------


def test_empty_candidate_set_handled_safely(filter_service: CandidateFilterService) -> None:
    """14. An empty candidate input produces a valid empty result without errors."""
    result = filter_service.filter_candidates([])
    assert isinstance(result, CandidateFilterResult)
    assert result.original_count == 0
    assert result.final_count == 0
    assert result.candidates == []


def test_all_filtered_out_handled_safely(filter_service: CandidateFilterService) -> None:
    """14b. When all candidates are filtered out, returns final_count=0 safely."""
    candidates = [
        _make_candidate(paper_id="shown_1"),
    ]
    result = filter_service.filter_candidates(
        candidates,
        previously_shown_ids={"shown_1"},
    )
    assert result.original_count == 1
    assert result.final_count == 0
    assert result.candidates == []


# ---------------------------------------------------------------------------
# 15. No scoring / ranking performed
# ---------------------------------------------------------------------------


def test_filtering_does_not_score_or_rank(filter_service: CandidateFilterService) -> None:
    """15. Filter preserves original order and does not inject scores or rank papers."""
    candidates = [
        _make_candidate(paper_id="p3", citation_count=50),
        _make_candidate(paper_id="p1", citation_count=100),
        _make_candidate(paper_id="p2", citation_count=30),
    ]
    result = filter_service.filter_candidates(candidates)

    # Order preserved exactly as passed
    assert [c.paper_id for c in result.candidates] == ["p3", "p1", "p2"]
    # Candidate model has no score field
    assert not hasattr(result.candidates[0], "score")


# ---------------------------------------------------------------------------
# 16. Leading Thinker remains unimplemented
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_leading_thinker_strategy_generates_candidates() -> None:
    """16. LeadingThinkerStrategy returns candidates with thinker metadata."""
    context = SpotlightUserContext(
        user_id=uuid4(),
        major="Economics",
        extracted_keywords={"major": ["Economics"]},
    )
    fake_papers = {
        "data": [
            {
                "paperId": "p_econ",
                "title": "Development Economics",
                "abstract": "A study of development economics and welfare.",
                "authors": [{"authorId": "a_econ", "name": "Amartya Sen"}],
                "citationCount": 5000,
                "url": "https://example.com/econ",
                "openAccessPdf": {
                    "url": "https://example.com/econ.pdf",
                    "status": "GOLD",
                    "license": "CC-BY",
                },
            }
        ]
    }
    async def mock_search(query, limit=None, **kwargs):
        return fake_papers, 200

    async def mock_author_lookup(ids):
        return {"a_econ": {"authorId": "a_econ", "name": "Amartya Sen", "citationCount": 80000, "hIndex": 90, "paperCount": 200}}

    strat = LeadingThinkerStrategy(search_fn=mock_search, author_lookup_fn=mock_author_lookup)
    candidates = await strat.get_candidates(context)
    assert len(candidates) == 1
    assert candidates[0].paper_id == "p_econ"
    assert "thinker_score" in candidates[0].metadata



# ---------------------------------------------------------------------------
# 17. Beyond Your Field deterministic filtering
# ---------------------------------------------------------------------------


def test_beyond_your_field_excludes_exact_major_field(
    filter_service: CandidateFilterService,
) -> None:
    """Beyond Your Field excludes papers whose fieldsOfStudy is exclusively the user's major."""
    context = SpotlightUserContext(
        user_id=uuid4(),
        major="Computer Science",
        minor="Mathematics",
    )
    candidates = [
        _make_candidate(
            paper_id="p_cs_only",
            spotlight_type=SpotlightType.beyond_your_field,
            fields_of_study=["Computer Science"],
        ),
        _make_candidate(
            paper_id="p_bio",
            spotlight_type=SpotlightType.beyond_your_field,
            fields_of_study=["Biology", "Medicine"],
        ),
    ]
    result = filter_service.filter_candidates(candidates, context=context)

    assert result.category_filtered_count == 1
    assert result.final_count == 1
    assert result.candidates[0].paper_id == "p_bio"


# ---------------------------------------------------------------------------
# 18. Structured Result Model verification
# ---------------------------------------------------------------------------


def test_structured_filter_result_shape(filter_service: CandidateFilterService) -> None:
    """Result matches the expected diagnostic structure."""
    candidates = [
        _make_candidate(paper_id="p1", citation_count=50),
        _make_candidate(paper_id="p1"),  # dup
        _make_candidate(paper_id="", title=""),  # invalid
        _make_candidate(paper_id="shown"),  # shown
        _make_candidate(paper_id="p_low", citation_count=5),  # low citation
    ]
    result = filter_service.filter_candidates(
        candidates,
        previously_shown_ids={"shown"},
    )

    dumped = result.model_dump()
    assert dumped["original_count"] == 5
    assert dumped["duplicate_count"] == 1
    assert dumped["invalid_count"] == 1
    assert dumped["previously_shown_count"] == 1
    assert dumped["category_filtered_count"] == 1
    assert dumped["final_count"] == 1
    assert len(dumped["candidates"]) == 1


# ---------------------------------------------------------------------------
# 19. Async DB History Extraction (V2 only, ignoring V1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_user_spotlight_history_from_profile_and_logs(mock_db) -> None:
    """Extracts previously shown/read/saved from Profile and V2 logs; ignores V1."""
    user_id = uuid4()

    # Profile with a V2 spotlight marked as read
    mock_profile = type(
        "Profile",
        (),
        {
            "user_id": user_id,
            "learning_spotlight": {
                "version": 2,
                "paper": {"paper_id": "current_paper_1"},
                "engagement": {"is_read": True, "is_saved": False},
            },
        },
    )()

    # Historical logs: one V2 saved, one V1 (ignored)
    mock_log_v2 = type(
        "LearningRecommendationLog",
        (),
        {
            "user_id": user_id,
            "learning_recommendations": {
                "version": 2,
                "paper": {"paper_id": "history_paper_2"},
                "engagement": {"is_read": False, "is_saved": True},
            },
        },
    )()

    mock_log_v1 = type(
        "LearningRecommendationLog",
        (),
        {
            "user_id": user_id,
            "learning_recommendations": {
                # V1 structure (no version=2)
                "result": {"data": [{"paperId": "v1_paper_should_be_ignored"}]},
            },
        },
    )()

    from tests.unit.conftest import FakeScalarResult

    db = mock_db(
        FakeScalarResult(mock_profile),
        FakeScalarResult(values=[mock_log_v2, mock_log_v1]),
    )

    shown, read, saved = await get_user_spotlight_history(db, user_id)

    assert "current_paper_1" in shown
    assert "current_paper_1" in read
    assert "current_paper_1" not in saved

    assert "history_paper_2" in shown
    assert "history_paper_2" not in read
    assert "history_paper_2" in saved

    assert "v1_paper_should_be_ignored" not in shown


@pytest.mark.asyncio
async def test_filter_for_user_with_db(filter_service: CandidateFilterService, mock_db) -> None:
    """filter_for_user seamlessly integrates DB history extraction with candidate filtering."""
    user_id = uuid4()
    mock_profile = type(
        "Profile",
        (),
        {
            "user_id": user_id,
            "learning_spotlight": {
                "version": 2,
                "paper": {"paper_id": "p_shown"},
                "engagement": {"is_read": True, "is_saved": False},
            },
        },
    )()

    from tests.unit.conftest import FakeScalarResult

    db = mock_db(
        FakeScalarResult(mock_profile),
        FakeScalarResult(values=[]),
    )

    candidates = [
        _make_candidate(paper_id="p_shown"),
        _make_candidate(paper_id="p_new"),
    ]

    result = await filter_service.filter_for_user(db, user_id, candidates)

    assert result.original_count == 2
    assert result.previously_shown_count == 1
    assert result.previously_read_count == 1
    assert result.final_count == 1
    assert result.candidates[0].paper_id == "p_new"


# ---------------------------------------------------------------------------
# Duplicate paperId exclusion (previously recommended)
# ---------------------------------------------------------------------------


def test_previously_recommended_paper_is_removed(
    filter_service: CandidateFilterService,
) -> None:
    """TEST 1: Previously recommended paperId is removed from candidates."""
    candidates = [
        _make_candidate(paper_id="abc123"),
        _make_candidate(paper_id="def456"),
    ]
    result = filter_service.filter_candidates(
        candidates,
        previously_shown_ids={"abc123"},
    )

    assert [c.paper_id for c in result.candidates] == ["def456"]
    assert result.previously_shown_count == 1
    assert result.final_count == 1


def test_multiple_previously_recommended_papers_are_removed(
    filter_service: CandidateFilterService,
) -> None:
    """TEST 2: Multiple previously recommended paperIds are removed."""
    candidates = [
        _make_candidate(paper_id="abc123"),
        _make_candidate(paper_id="def456"),
        _make_candidate(paper_id="xyz789"),
        _make_candidate(paper_id="new999"),
    ]
    result = filter_service.filter_candidates(
        candidates,
        previously_shown_ids={"abc123", "xyz789"},
    )

    assert [c.paper_id for c in result.candidates] == ["def456", "new999"]
    assert result.previously_shown_count == 2
    assert result.final_count == 2


def test_previously_recommended_normalization_case_and_whitespace(
    filter_service: CandidateFilterService,
) -> None:
    """TEST 3: Case/whitespace differences still exclude the same paperId."""
    candidates = [
        _make_candidate(paper_id="abc123"),
        _make_candidate(paper_id="def456"),
    ]
    result = filter_service.filter_candidates(
        candidates,
        previously_shown_ids={" ABC123 "},
    )

    assert [c.paper_id for c in result.candidates] == ["def456"]
    assert result.previously_shown_count == 1


def test_missing_paper_id_cannot_become_final_recommendation(
    filter_service: CandidateFilterService,
) -> None:
    """TEST 4: Candidates without a valid paperId are invalid and never selected."""
    candidates = [
        _make_candidate(paper_id="", title="No ID"),
        SpotlightCandidate(
            paper_id="   ",
            title="Whitespace ID",
            authors=[LearningSpotlightAuthor(author_id="a1", name="Author")],
            abstract="Sample abstract",
            venue="Conference",
            year=2026,
            citation_count=25,
            url="https://example.com/ws",
            query='("AI")',
            spotlight_type=SpotlightType.influential_research,
            metadata={},
        ),
        _make_candidate(paper_id="valid_only"),
    ]
    result = filter_service.filter_candidates(candidates)

    assert result.invalid_count == 2
    assert [c.paper_id for c in result.candidates] == ["valid_only"]
    assert all(_normalize_paper_id(c.paper_id) for c in result.candidates)


def test_no_previous_recommendations_keeps_all_valid(
    filter_service: CandidateFilterService,
) -> None:
    """TEST 5: With no prior recommendations, all valid candidates remain."""
    candidates = [
        _make_candidate(paper_id="abc123"),
        _make_candidate(paper_id="def456"),
    ]
    result = filter_service.filter_candidates(
        candidates,
        previously_shown_ids=set(),
    )

    assert [c.paper_id for c in result.candidates] == ["abc123", "def456"]
    assert result.previously_shown_count == 0
    assert result.final_count == 2


def test_all_candidates_previously_recommended_yields_empty(
    filter_service: CandidateFilterService,
) -> None:
    """TEST 6: If every candidate was already recommended, none remain."""
    candidates = [
        _make_candidate(paper_id="abc123"),
        _make_candidate(paper_id="def456"),
    ]
    result = filter_service.filter_candidates(
        candidates,
        previously_shown_ids={"abc123", "def456"},
    )

    assert result.candidates == []
    assert result.final_count == 0
    assert result.previously_shown_count == 2


def test_previously_recommended_removed_before_ranking(
    filter_service: CandidateFilterService,
) -> None:
    """TEST 7: Previously recommended top-ranked paper is removed before scoring."""
    context = SpotlightUserContext(
        user_id=uuid4(),
        major="Computer Science",
        interests=["deep learning"],
        extracted_keywords={
            "major": ["Computer Science"],
            "interests": ["deep learning"],
            "engagement_keywords": {"transformers": 5},
        },
    )
    # Would normally rank highest due to title/abstract relevance + citations
    previously_recommended = _make_candidate(
        paper_id="abc123",
        title="Transformers for deep learning in computer science",
        abstract="A comprehensive study of transformers and deep learning methods.",
        citation_count=500,
        year=2026,
    )
    remaining_best = _make_candidate(
        paper_id="def456",
        title="Transformers for deep learning applications",
        abstract="Research on transformers applied to deep learning problems.",
        citation_count=100,
        year=2025,
    )
    weaker = _make_candidate(
        paper_id="low999",
        title="An unrelated botanical survey",
        abstract="Plant taxonomy and soil composition in arid climates.",
        citation_count=10,
        year=2020,
    )

    filtered = filter_service.filter_candidates(
        [previously_recommended, remaining_best, weaker],
        previously_shown_ids={"abc123"},
        context=context,
    )
    assert [c.paper_id for c in filtered.candidates] == ["def456", "low999"]

    ranking = CandidateScoringService().score_and_rank_candidates(
        filtered.candidates,
        context,
        reference_date=date(2026, 9, 24),
    )
    assert ranking.selected_candidate is not None
    assert ranking.selected_candidate.paper_id == "def456"
    assert "abc123" not in {c.paper_id for c in ranking.ranked_candidates}


@pytest.mark.asyncio
async def test_get_user_spotlight_history_from_papers_array(mock_db) -> None:
    """History extraction reads multi-paper ``papers`` snapshots (current format)."""
    user_id = uuid4()

    mock_profile = type(
        "Profile",
        (),
        {
            "user_id": user_id,
            "learning_spotlight": {
                "version": 2,
                "papers": [
                    {
                        "paper_id": " ABC123 ",
                        "title": "Current Paper",
                        "engagement": {"is_read": True, "is_saved": False},
                    },
                    {
                        "paper_id": "def456",
                        "title": "Second Paper",
                        "engagement": {"is_read": False, "is_saved": True},
                    },
                ],
            },
        },
    )()

    mock_log_v2 = type(
        "LearningRecommendationLog",
        (),
        {
            "user_id": user_id,
            "learning_recommendations": {
                "version": 2,
                "papers": [
                    {
                        "paper_id": "xyz789",
                        "title": "Archived Paper",
                        "engagement": {"is_read": False, "is_saved": False},
                    }
                ],
            },
        },
    )()

    from tests.unit.conftest import FakeScalarResult

    db = mock_db(
        FakeScalarResult(mock_profile),
        FakeScalarResult(values=[mock_log_v2]),
    )

    shown, read, saved = await get_user_spotlight_history(db, user_id)

    assert shown == {"abc123", "def456", "xyz789"}
    assert read == {"abc123"}
    assert saved == {"def456"}


@pytest.mark.asyncio
async def test_filter_for_user_excludes_papers_array_history(
    filter_service: CandidateFilterService,
    mock_db,
) -> None:
    """filter_for_user excludes paperIds from the current multi-paper snapshot."""
    user_id = uuid4()
    mock_profile = type(
        "Profile",
        (),
        {
            "user_id": user_id,
            "learning_spotlight": {
                "version": 2,
                "papers": [
                    {
                        "paper_id": "p_shown",
                        "engagement": {"is_read": False, "is_saved": False},
                    }
                ],
            },
        },
    )()

    from tests.unit.conftest import FakeScalarResult

    db = mock_db(
        FakeScalarResult(mock_profile),
        FakeScalarResult(values=[]),
    )

    candidates = [
        _make_candidate(paper_id="P_SHOWN"),
        _make_candidate(paper_id="p_new"),
    ]

    result = await filter_service.filter_for_user(db, user_id, candidates)

    assert result.previously_shown_count == 1
    assert [c.paper_id for c in result.candidates] == ["p_new"]

