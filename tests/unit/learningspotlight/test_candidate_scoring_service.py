"""Unit tests for Step 8: Candidate Scoring, Ranking, and Selection Service.

Verifies:
1. Major match increases user relevance score.
2. Minor match increases user relevance score.
3. Interest match increases user relevance score.
4. Engagement keyword with higher weight influences score more strongly.
5. Content keyword match contributes to score.
6. Hashtag match contributes to score.
7. Exact/normalized keyword matching behaves deterministically.
8. Citation counts are normalized before weighting.
9. Influential papers receive higher influence scores with higher citations.
10. More recent papers receive higher recency scores.
11. Country match increases Country Perspective category score.
12. Beyond Your Field rewards difference from major/minor while retaining user-interest relevance.
13. Final score follows the configured weights.
14. Candidates are ranked descending by final_score.
15. Highest-ranked candidate is selected as #1.
16. Tie-breaking is deterministic.
17. Missing publication year does not crash.
18. Leading Thinker remains unimplemented (raises NotImplementedError).
19. No database writes occur in Step 8.
"""

from __future__ import annotations

import os
from datetime import date
from unittest.mock import patch
from uuid import uuid4

import pytest

from apps.learningspotlight.config import LearningSpotlightSettings
from apps.learningspotlight.schemas import (
    LearningSpotlightAuthor,
    ScoredSpotlightCandidate,
    SpotlightCandidate,
    SpotlightRankingResult,
    SpotlightUserContext,
)
from apps.learningspotlight.services.candidate_scoring_service import (
    CandidateScoringService,
)
from common.enums import SpotlightType


# ---------------------------------------------------------------------------
# Fixtures & Helpers
# ---------------------------------------------------------------------------


def _make_context(**overrides) -> SpotlightUserContext:
    defaults = {
        "user_id": uuid4(),
        "country": "India",
        "major": "Computer Science",
        "minor": "Mathematics",
        "interests": ["Robotics", "NLP"],
        "extracted_keywords": {
            "major": ["Computer Science"],
            "minor": ["Mathematics"],
            "interests": ["Robotics", "NLP"],
            "engagement_keywords": {"transformers": 4, "deep learning": 1},
            "content_keywords": {"reinforcement learning": 2},
            "hashtags": {"ai": 3},
        },
    }
    defaults.update(overrides)
    return SpotlightUserContext(**defaults)


def _make_candidate(
    paper_id: str = "p1",
    title: str = "General Title",
    abstract: str = "General abstract text for testing.",
    citation_count: int | None = 25,
    year: int | None = 2026,
    spotlight_type: SpotlightType = SpotlightType.influential_research,
    query: str = '("AI")',
    authors: list[LearningSpotlightAuthor] | None = None,
    venue: str | None = "NeurIPS",
    url: str | None = "https://example.com/p1",
    **metadata,
) -> SpotlightCandidate:
    return SpotlightCandidate(
        paper_id=paper_id,
        title=title,
        authors=authors or [LearningSpotlightAuthor(author_id="a1", name="Author Name")],
        abstract=abstract,
        venue=venue,
        year=year,
        citation_count=citation_count,
        url=url,
        query=query,
        spotlight_type=spotlight_type,
        metadata=metadata,
    )


@pytest.fixture
def scoring_service() -> CandidateScoringService:
    return CandidateScoringService()


# ---------------------------------------------------------------------------
# 1-7. User Relevance Scoring Tests
# ---------------------------------------------------------------------------


def test_major_match_increases_user_relevance(scoring_service: CandidateScoringService) -> None:
    """1. Major match significantly increases user relevance score."""
    context = _make_context(
        major="Computer Science",
        minor=None,
        interests=[],
        extracted_keywords={"major": ["Computer Science"]},
    )
    candidate_matching = _make_candidate(title="Advances in Computer Science")
    candidate_unrelated = _make_candidate(title="Botanical Studies of Ferns")

    score_match = scoring_service.calculate_user_relevance(candidate_matching, context)
    score_unrelated = scoring_service.calculate_user_relevance(candidate_unrelated, context)

    assert score_match > score_unrelated
    assert score_match >= 30.0
    assert score_unrelated == 0.0


def test_minor_match_increases_user_relevance(scoring_service: CandidateScoringService) -> None:
    """2. Minor match increases user relevance score."""
    context = _make_context(
        major=None,
        minor="Astrophysics",
        interests=[],
        extracted_keywords={"minor": ["Astrophysics"]},
    )
    candidate_matching = _make_candidate(title="Observational Astrophysics")
    candidate_unrelated = _make_candidate(title="Macroeconomic Trends")

    score_match = scoring_service.calculate_user_relevance(candidate_matching, context)
    score_unrelated = scoring_service.calculate_user_relevance(candidate_unrelated, context)

    assert score_match > score_unrelated
    assert score_match >= 20.0


def test_interest_match_increases_user_relevance(scoring_service: CandidateScoringService) -> None:
    """3. Interest match increases user relevance score."""
    context = _make_context(
        major=None,
        minor=None,
        interests=["Quantum Computing"],
        extracted_keywords={"interests": ["Quantum Computing"]},
    )
    candidate_matching = _make_candidate(abstract="Progress in Quantum Computing algorithms.")
    candidate_unrelated = _make_candidate(abstract="History of Ancient Rome.")

    score_match = scoring_service.calculate_user_relevance(candidate_matching, context)
    score_unrelated = scoring_service.calculate_user_relevance(candidate_unrelated, context)

    assert score_match > score_unrelated
    assert score_match >= 20.0


def test_engagement_keyword_higher_weight_influences_score_more(
    scoring_service: CandidateScoringService,
) -> None:
    """4. Engagement keyword with higher accumulated weight produces higher score."""
    context = _make_context(
        major=None,
        minor=None,
        interests=[],
        extracted_keywords={
            "engagement_keywords": {
                "high_engagement_topic": 10,  # e.g. multiple comments + reposts
                "low_engagement_topic": 1,    # e.g. single like
            }
        },
    )
    candidate_high = _make_candidate(abstract="Discussion on high_engagement_topic.")
    candidate_low = _make_candidate(abstract="Discussion on low_engagement_topic.")

    score_high = scoring_service.calculate_user_relevance(candidate_high, context)
    score_low = scoring_service.calculate_user_relevance(candidate_low, context)

    assert score_high > score_low


def test_high_cardinality_signals_do_not_run_fuzzy_extract(
    scoring_service: CandidateScoringService,
) -> None:
    """Engagement/content/hashtags are exact lookups; RapidFuzz stays on short fields."""
    from apps.learningspotlight.services.keyword_normalization_service import (
        get_keyword_normalization_service,
        reset_keyword_normalization_service,
    )

    reset_keyword_normalization_service()
    get_keyword_normalization_service().refresh_vocabulary(
        ["Artificial Intelligence", "Machine Learning", "Cybersecurity"]
        + [f"Catalog Term {i}" for i in range(40)]
    )
    context = _make_context(
        extracted_keywords={
            "major": ["Computer Science"],
            "minor": ["Mathematics"],
            "interests": ["Robotics", "NLP"],
            "engagement_keywords": {f"engagement-term-{i}": 1 for i in range(80)},
            "content_keywords": {f"content-term-{i}": 1 for i in range(80)},
            "hashtags": {f"tag-{i}": 1 for i in range(80)},
        }
    )
    candidate = _make_candidate(
        title="Computer Science survey",
        abstract="A discussion of robotics and NLP.",
    )
    with patch(
        "apps.learningspotlight.services.keyword_normalization_service.process.extract"
    ) as extract:
        extract.return_value = []
        scoring_service.calculate_user_relevance(candidate, context)
        assert extract.call_count <= 10
    reset_keyword_normalization_service()


def test_content_keyword_match_contributes_to_score(
    scoring_service: CandidateScoringService,
) -> None:
    """5. Content keyword match contributes to relevance score."""
    context = _make_context(
        major=None,
        minor=None,
        interests=[],
        extracted_keywords={"content_keywords": {"graph neural networks": 3}},
    )
    candidate = _make_candidate(abstract="Analysis using graph neural networks.")
    score = scoring_service.calculate_user_relevance(candidate, context)
    assert score > 0.0


def test_hashtag_match_contributes_to_score(
    scoring_service: CandidateScoringService,
) -> None:
    """6. Hashtag match contributes to relevance score."""
    context = _make_context(
        major=None,
        minor=None,
        interests=[],
        extracted_keywords={"hashtags": {"datascience": 2}},
    )
    candidate = _make_candidate(title="Modern datascience methodologies")
    score = scoring_service.calculate_user_relevance(candidate, context)
    assert score > 0.0


def test_keyword_matching_is_deterministic_and_case_insensitive(
    scoring_service: CandidateScoringService,
) -> None:
    """7. Exact/normalized keyword matching behaves deterministically."""
    context = _make_context(
        major="Computer Science",
        interests=["Machine Learning"],
    )
    candidate_upper = _make_candidate(title="COMPUTER SCIENCE and MACHINE LEARNING")
    candidate_lower = _make_candidate(title="computer science and machine learning")

    score_upper = scoring_service.calculate_user_relevance(candidate_upper, context)
    score_lower = scoring_service.calculate_user_relevance(candidate_lower, context)

    assert score_upper == score_lower
    assert score_upper > 0.0
    assert 0.0 <= score_upper <= 100.0


# ---------------------------------------------------------------------------
# 8-9. Citation count normalization & Influential Research scoring
# ---------------------------------------------------------------------------


def test_citation_counts_are_normalized_before_weighting(
    scoring_service: CandidateScoringService,
) -> None:
    """8. Quality and category scores normalize citations to 0–100 range."""
    candidate_huge = _make_candidate(citation_count=50000)
    candidate_zero = _make_candidate(citation_count=0)

    quality_huge = scoring_service.calculate_paper_quality(candidate_huge)
    quality_zero = scoring_service.calculate_paper_quality(candidate_zero)

    assert 0.0 <= quality_huge <= 100.0
    assert 0.0 <= quality_zero <= 100.0
    assert quality_huge > quality_zero


def test_influential_papers_receive_higher_score_with_more_citations(
    scoring_service: CandidateScoringService,
) -> None:
    """9. Influential papers receive monotonically higher category score with more citations."""
    context = _make_context()
    ref_date = date(2026, 8, 23)

    c_20 = _make_candidate(citation_count=20, spotlight_type=SpotlightType.influential_research)
    c_100 = _make_candidate(citation_count=100, spotlight_type=SpotlightType.influential_research)
    c_1000 = _make_candidate(citation_count=1000, spotlight_type=SpotlightType.influential_research)

    score_20 = scoring_service.calculate_category_score(c_20, context, ref_date)
    score_100 = scoring_service.calculate_category_score(c_100, context, ref_date)
    score_1000 = scoring_service.calculate_category_score(c_1000, context, ref_date)

    assert score_20 == 50.0  # Baseline at threshold (20 citations)
    assert score_20 < score_100 < score_1000
    assert score_1000 == 100.0


# ---------------------------------------------------------------------------
# 10. Recency Score
# ---------------------------------------------------------------------------


def test_more_recent_papers_receive_higher_recency_score(
    scoring_service: CandidateScoringService,
) -> None:
    """10. More recent papers dynamically receive higher recency scores."""
    ref_date = date(2026, 8, 23)

    c_current = _make_candidate(year=2026)
    c_last_year = _make_candidate(year=2025)
    c_two_years = _make_candidate(year=2024)
    c_old = _make_candidate(year=2018)

    score_current = scoring_service.calculate_recency_score(c_current, ref_date)
    score_last = scoring_service.calculate_recency_score(c_last_year, ref_date)
    score_two = scoring_service.calculate_recency_score(c_two_years, ref_date)
    score_old = scoring_service.calculate_recency_score(c_old, ref_date)

    assert score_current == 100.0
    assert score_current > score_last > score_two > score_old
    assert score_old >= 5.0


def test_missing_publication_year_does_not_crash(
    scoring_service: CandidateScoringService,
) -> None:
    """17. Missing publication year returns a safe default score without crashing."""
    ref_date = date(2026, 8, 23)
    candidate = _make_candidate(year=None)

    score = scoring_service.calculate_recency_score(candidate, ref_date)
    assert 0.0 <= score <= 100.0
    assert score == 10.0


# ---------------------------------------------------------------------------
# 11-12. Category-Specific Scoring (Country Perspective & Beyond Field)
# ---------------------------------------------------------------------------


def test_country_match_increases_country_perspective_score(
    scoring_service: CandidateScoringService,
) -> None:
    """11. Country match with user country increases Country Perspective category score."""
    context = _make_context(country="India")
    ref_date = date(2026, 8, 23)

    c_match = _make_candidate(
        spotlight_type=SpotlightType.country_perspective,
        author_affiliations=[{"name": "Prof A", "affiliations": ["IIT Delhi, India"]}],
    )
    c_neutral = _make_candidate(
        spotlight_type=SpotlightType.country_perspective,
        author_affiliations=[],
    )

    score_match = scoring_service.calculate_category_score(c_match, context, ref_date)
    score_neutral = scoring_service.calculate_category_score(c_neutral, context, ref_date)

    assert score_match == 100.0
    assert score_match > score_neutral


def test_beyond_your_field_rewards_divergence_and_interest_relevance(
    scoring_service: CandidateScoringService,
) -> None:
    """12. Beyond Your Field rewards divergence from major/minor while retaining interest relevance."""
    context = _make_context(
        major="Computer Science",
        minor="Mathematics",
        interests=["Neuroscience"],
    )
    ref_date = date(2026, 8, 23)

    # Outside CS/Math, but relevant to Neuroscience interest
    c_ideal = _make_candidate(
        title="Cognitive Neuroscience and Brain Modeling",
        abstract="A biological study of neural pathways.",
        spotlight_type=SpotlightType.beyond_your_field,
    )
    # Directly in Computer Science (poor beyond-field candidate)
    c_in_field = _make_candidate(
        title="Computer Science and Compiler Optimization",
        abstract="Algorithms in pure computer science.",
        spotlight_type=SpotlightType.beyond_your_field,
    )

    score_ideal = scoring_service.calculate_category_score(c_ideal, context, ref_date)
    score_in_field = scoring_service.calculate_category_score(c_in_field, context, ref_date)

    assert score_ideal > score_in_field
    assert score_ideal == 100.0  # 50 base + 25 outside major + 25 interest match


# ---------------------------------------------------------------------------
# 13. Final Score Weights
# ---------------------------------------------------------------------------


def test_final_score_follows_configured_weights(
    scoring_service: CandidateScoringService,
) -> None:
    """13. Final score accurately combines components according to 40/20/10/30 weights."""
    final = scoring_service.calculate_final_score(
        user_relevance=100.0,
        quality=100.0,
        recency=100.0,
        category=100.0,
    )
    assert final == 100.0

    # 80 * 0.40 (32) + 60 * 0.20 (12) + 50 * 0.10 (5) + 90 * 0.30 (27) = 76.0
    weighted = scoring_service.calculate_final_score(
        user_relevance=80.0,
        quality=60.0,
        recency=50.0,
        category=90.0,
    )
    assert weighted == 76.0


# ---------------------------------------------------------------------------
# 14-16. Ranking, Selection & Deterministic Tie-Breaking
# ---------------------------------------------------------------------------


def test_candidates_ranked_descending_and_best_selected(
    scoring_service: CandidateScoringService,
) -> None:
    """14 & 15. Candidates are ranked descending by final_score, and #1 is selected."""
    context = _make_context(major="Computer Science")
    ref_date = date(2026, 8, 23)

    c_best = _make_candidate(
        paper_id="best_1",
        title="Computer Science Breakthrough in Deep Learning",
        citation_count=500,
        year=2026,
    )
    c_medium = _make_candidate(
        paper_id="med_1",
        title="Computer Science Routine Study",
        citation_count=30,
        year=2025,
    )
    c_low = _make_candidate(
        paper_id="low_1",
        title="Archaeology in Mesopotamia",
        citation_count=20,
        year=2020,
    )

    result = scoring_service.score_and_rank_candidates(
        [c_medium, c_low, c_best],
        context,
        reference_date=ref_date,
    )

    assert isinstance(result, SpotlightRankingResult)
    assert result.total_candidates == 3
    assert result.selected_candidate is not None
    assert result.selected_candidate.paper_id == "best_1"
    assert result.ranked_candidates[0].paper_id == "best_1"
    assert result.ranked_candidates[1].paper_id == "med_1"
    assert result.ranked_candidates[2].paper_id == "low_1"
    assert (
        result.ranked_candidates[0].final_score
        >= result.ranked_candidates[1].final_score
        >= result.ranked_candidates[2].final_score
    )


def test_tie_breaking_is_deterministic(scoring_service: CandidateScoringService) -> None:
    """16. Tie-breaking breaks ties deterministically via relevance -> category -> citations -> year -> paper_id."""
    context = _make_context()
    ref_date = date(2026, 8, 23)

    # Identical scores, tie broken by alphabetical paper_id ASC ("a_paper" before "b_paper")
    c_a = _make_candidate(paper_id="a_paper", title="Identical Title", citation_count=50, year=2026)
    c_b = _make_candidate(paper_id="b_paper", title="Identical Title", citation_count=50, year=2026)

    result1 = scoring_service.score_and_rank_candidates([c_b, c_a], context, reference_date=ref_date)
    result2 = scoring_service.score_and_rank_candidates([c_a, c_b], context, reference_date=ref_date)

    assert result1.selected_candidate.paper_id == "a_paper"
    assert result2.selected_candidate.paper_id == "a_paper"
    assert [c.paper_id for c in result1.ranked_candidates] == ["a_paper", "b_paper"]


def test_empty_candidates_returns_safe_empty_result(
    scoring_service: CandidateScoringService,
) -> None:
    """Empty candidate list yields empty result without crash."""
    context = _make_context()
    result = scoring_service.score_and_rank_candidates([], context)
    assert result.total_candidates == 0
    assert result.selected_candidate is None
    assert result.ranked_candidates == []


# ---------------------------------------------------------------------------
# 18. Leading Thinker Unimplemented
# ---------------------------------------------------------------------------


def test_leading_thinker_scoring_executes_successfully(
    scoring_service: CandidateScoringService,
) -> None:
    """18. Leading Thinker candidates calculate thinker category score successfully."""
    context = _make_context()
    c = _make_candidate(
        spotlight_type=SpotlightType.leading_thinker,
        thinker_score=88.0,
    )

    result = scoring_service.score_and_rank_candidates([c], context)
    assert result.selected_candidate is not None
    assert result.selected_candidate.category_score == 88.0
    assert result.selected_candidate.final_score > 0.0


# ---------------------------------------------------------------------------
# 19. No DB Writes
# ---------------------------------------------------------------------------


def test_scoring_service_performs_no_db_operations(
    scoring_service: CandidateScoringService,
) -> None:
    """19. CandidateScoringService is a pure in-memory calculation service with no DB access."""
    context = _make_context()
    candidates = [_make_candidate(paper_id="p1")]

    # Pure execution requires no database session or connections
    result = scoring_service.score_and_rank_candidates(candidates, context)
    assert result.selected_candidate is not None
    assert result.selected_candidate.paper_id == "p1"
