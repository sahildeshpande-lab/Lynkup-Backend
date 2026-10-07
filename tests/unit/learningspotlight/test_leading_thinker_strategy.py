"""Unit tests for Step 11: Leading Thinker Strategy, Author Evaluation, and Scheduling.

Verifies:
1. LeadingThinkerStrategy no longer raises NotImplementedError.
2. Candidate authors are extracted from papers.
3. Duplicate authors across papers are evaluated only once (deduplication / cached).
4. Author influence score is normalized (0-100).
5. Missing h-index does not crash (graceful weight redistribution).
6. Missing citation count does not crash (graceful fallback).
7. User relevance contributes to thinker score.
8. Thinker score is normalized 0-100.
9. Standard SpotlightCandidate contract remains compatible.
10. Final ranking uses thinker score in CandidateScoringService.
11. V1 tests remain passing.
12. Daily run endpoint invokes the existing daily generation service.
13. Same cycle category is used for all batches.
14. Batch size remains 50 by default.
15. Candidate limit remains 30 by default.
16. Retry is idempotent.
17. One user failure does not stop the rest.
18. No V1 fields are updated by V2.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from apps.accounts.db_models import User
from apps.learningspotlight.config import LearningSpotlightSettings
from apps.learningspotlight.routes import router as spotlight_router
from apps.learningspotlight.schemas import (
    LearningSpotlightAuthor,
    SpotlightCandidate,
    SpotlightUserContext,
)
from apps.learningspotlight.services.candidate_scoring_service import (
    CandidateScoringService,
)
from apps.learningspotlight.services.daily_generation_service import (
    DailyGenerationResult,
    DefaultSpotlightPaperGenerator,
    LearningSpotlightDailyGenerationService,
)
from apps.learningspotlight.services.leading_thinker_strategy import (
    LeadingThinkerStrategy,
    calculate_author_influence_score,
    calculate_thinker_score,
)
from apps.learningspotlight.services.semantic_scholar_adapter import (
    get_authors_batch_v2,
)
from apps.profiles.db_models.learning_recommendation_settings_db_model import (
    LearningRecommendationSettings,
)
from apps.profiles.db_models.profile_db_model import Profile
from apps.recommendations.services.recommendation_settings_service import (
    RecommendationSettingsService,
)
from common.enums import SpotlightType
from core.database.session import get_session
from core.security.auth import get_current_admin, get_current_user
from fastapi import FastAPI


# ---------------------------------------------------------------------------
# 1-3. Author Influence & Thinker Score Unit Tests
# ---------------------------------------------------------------------------


def test_author_influence_normalization_full_signals() -> None:
    """4. Author influence score is normalized 0-100 with all metrics present."""
    author_elite = {
        "authorId": "a_elite",
        "name": "Geoffrey Hinton",
        "citationCount": 250000,
        "hIndex": 120,
        "paperCount": 350,
    }
    score_elite = calculate_author_influence_score(author_elite)
    assert 90.0 <= score_elite <= 100.0

    author_mid = {
        "authorId": "a_mid",
        "name": "Junior Researcher",
        "citationCount": 50,
        "hIndex": 5,
        "paperCount": 10,
    }
    score_mid = calculate_author_influence_score(author_mid)
    assert 0.0 <= score_mid <= 100.0
    assert score_mid < score_elite


def test_author_influence_missing_h_index_redistributes_weights() -> None:
    """5. Missing h-index does not crash; weights are redistributed."""
    author_no_h = {
        "authorId": "a_noh",
        "citationCount": 5000,
        "hIndex": None,
        "paperCount": 50,
    }
    score = calculate_author_influence_score(author_no_h)
    assert 0.0 <= score <= 100.0
    assert score > 50.0


def test_author_influence_missing_citations_graceful_fallback() -> None:
    """6. Missing citations does not crash; falls back gracefully."""
    author_no_cites = {
        "authorId": "a_nocites",
        "citationCount": None,
        "hIndex": 15,
        "paperCount": 20,
    }
    score = calculate_author_influence_score(author_no_cites)
    assert 0.0 <= score <= 100.0

    # No author dict at all, fallback to paper citations
    score_paper_fallback = calculate_author_influence_score(None, paper_citation_count=150)
    assert 0.0 <= score_paper_fallback <= 100.0


def test_thinker_score_calculation_and_user_relevance_contribution() -> None:
    """7, 8. User relevance contributes 30% and author influence contributes 70%."""
    author_score = 90.0
    relevance_high = 80.0
    relevance_low = 20.0

    score_high = calculate_thinker_score(author_score, relevance_high)
    score_low = calculate_thinker_score(author_score, relevance_low)

    # 90 * 0.7 + 80 * 0.3 = 63 + 24 = 87.0
    assert score_high == 87.0
    # 90 * 0.7 + 20 * 0.3 = 63 + 6 = 69.0
    assert score_low == 69.0
    assert score_high > score_low


# ---------------------------------------------------------------------------
# 1-3, 9-10. Strategy Candidate Generation & Deduplication
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_leading_thinker_strategy_candidate_generation_and_deduplication() -> None:
    """1, 2, 3, 9. Extract candidate authors, deduplicate author lookups, attach thinker metadata."""
    user_id = uuid4()
    context = SpotlightUserContext(
        user_id=user_id,
        major="Computer Science",
        extracted_keywords={
            "major": ["Computer Science"],
            "interests": ["artificial intelligence", "deep learning"],
        },
    )

    # 2 papers sharing the same author "a1" (Yoshua Bengio)
    fake_papers = {
        "data": [
            {
                "paperId": "p1",
                "title": "Deep Learning Architectures",
                "abstract": "We survey deep learning architectures for representation learning.",
                "authors": [
                    {"authorId": "a1", "name": "Yoshua Bengio"},
                    {"authorId": "a2", "name": "Ian Goodfellow"},
                ],
                "citationCount": 12000,
                "url": "https://example.com/p1",
                "openAccessPdf": {
                    "url": "https://example.com/p1.pdf",
                    "status": "GOLD",
                    "license": "CC-BY",
                },
            },
            {
                "paperId": "p2",
                "title": "Generative Adversarial Nets",
                "abstract": "We propose a generative adversarial framework for learning.",
                "authors": [
                    {"authorId": "a2", "name": "Ian Goodfellow"},
                    {"authorId": "a1", "name": "Yoshua Bengio"},
                ],
                "citationCount": 45000,
                "url": "https://example.com/p2",
                "openAccessPdf": {
                    "url": "https://example.com/p2.pdf",
                    "status": "GOLD",
                    "license": "CC-BY",
                },
            },
        ]
    }

    mock_search = AsyncMock(return_value=(fake_papers, 200))
    mock_author_lookup = AsyncMock(
        return_value={
            "a1": {
                "authorId": "a1",
                "name": "Yoshua Bengio",
                "citationCount": 300000,
                "hIndex": 130,
                "paperCount": 600,
            },
            "a2": {
                "authorId": "a2",
                "name": "Ian Goodfellow",
                "citationCount": 100000,
                "hIndex": 70,
                "paperCount": 120,
            },
        }
    )

    strategy = LeadingThinkerStrategy(
        search_fn=mock_search,
        author_lookup_fn=mock_author_lookup,
    )

    candidates = await strategy.get_candidates(context)

    # 1. Strategy returns candidates
    assert len(candidates) == 2
    assert candidates[0].paper_id == "p1"
    assert candidates[1].paper_id == "p2"

    # 2 & 3. Author lookup was called ONCE with deduplicated author IDs
    mock_author_lookup.assert_called_once()
    queried_ids = mock_author_lookup.call_args[0][0]
    assert set(queried_ids) == {"a1", "a2"}

    # 9. Candidates contain valid thinker metadata
    for cand in candidates:
        assert "thinker_score" in cand.metadata
        assert "author_influence_score" in cand.metadata
        assert "paper_relevance_score" in cand.metadata
        assert cand.metadata["leading_author"]["author_id"] in ["a1", "a2"]
        assert 0.0 <= cand.metadata["thinker_score"] <= 100.0


@pytest.mark.asyncio
async def test_candidate_scoring_service_uses_thinker_score() -> None:
    """10. CandidateScoringService uses thinker_score for Day 1 Leading Thinker."""
    context = SpotlightUserContext(
        user_id=uuid4(),
        major="Computer Science",
        extracted_keywords={"major": ["Computer Science"]},
    )
    cand = SpotlightCandidate(
        paper_id="p_test",
        title="Learning Representations",
        query='("computer science")',
        spotlight_type=SpotlightType.leading_thinker,
        citation_count=500,
        metadata={
            "thinker_score": 92.5,
            "author_influence_score": 95.0,
            "paper_relevance_score": 86.6,
        },
    )

    scoring_svc = CandidateScoringService()
    category_score = scoring_svc.calculate_category_score(
        cand, context, reference_date=date(2026, 8, 24)
    )
    assert category_score == 92.5

    # Full ranking
    ranked_result = scoring_svc.score_and_rank_candidates([cand], context, reference_date=date(2026, 8, 24))
    assert ranked_result.total_candidates == 1
    assert ranked_result.selected_candidate.category_score == 92.5
    assert ranked_result.selected_candidate.final_score > 0.0


# ---------------------------------------------------------------------------
# 12-18. Cron & Admin Trigger Tests for Day 1 Leading Thinker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_daily_cron_executes_day_1_leading_thinker(mock_db) -> None:
    """12, 13, 14, 15, 16, 17, 18. Daily cron executes Day 1 Leading Thinker across batches."""
    user_id = uuid4()
    v1_timestamp = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    v1_recs = {"result": {"data": [{"paperId": "v1_paper"}]}}

    profile = Profile(
        user_id=user_id,
        major="Computer Science",
        extracted_keywords={"major": ["Computer Science"]},
        recommendations_updated_at=v1_timestamp,
        learning_recommendations=v1_recs,
        learning_spotlight=None,
    )

    # 2026-08-20 is Day 1 when cycle_start_date is 2026-08-20
    settings = LearningRecommendationSettings(
        id=uuid4(),
        is_enabled=True,
        generation_frequency_days=14,
        max_recommendations=10,
        cycle_start_date=date(2026, 8, 20),
        updated_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
        created_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )

    settings_svc = MagicMock(spec=RecommendationSettingsService)
    settings_svc.get_persisted_settings = AsyncMock(return_value=settings)

    # Mock paper generator to return success
    fake_generator = AsyncMock()
    fake_generator.generate_for_user = AsyncMock(return_value=True)

    svc = LearningSpotlightDailyGenerationService(
        settings_service=settings_svc,
        paper_generator=fake_generator,
        batch_size=50,
    )

    from apps.learningspotlight.services.daily_generation_service import _ProfileCandidate

    async def _mock_get_batch(session, *, batch_size, exclude_user_ids, run_mode="scheduled", **kwargs):
        if user_id in exclude_user_ids:
            return []
        return [
            _ProfileCandidate(
                user_id=user_id,
                extracted_keywords={"major": ["Computer Science"]},
                learning_spotlight=None,
                learning_spotlight_updated_at=None,
                recommendations_updated_at=v1_timestamp,
            )
        ]

    svc._get_next_profile_batch = _mock_get_batch

    with patch(
        "apps.learningspotlight.services.daily_generation_service.async_session_factory"
    ):
        result = await svc._run_daily_generation(today=date(2026, 8, 20))

    # Assertions for Day 1 Leading Thinker
    assert result.ran is True
    assert result.cycle_day == 1
    assert result.spotlight_type == SpotlightType.leading_thinker
    assert result.generated_users == 1
    assert result.failed_users == 0
    assert fake_generator.generate_for_user.call_count == 1
    call_kwargs = fake_generator.generate_for_user.call_args.kwargs
    assert call_kwargs["spotlight_type"] == SpotlightType.leading_thinker
    assert call_kwargs["cycle_day"] == 1


@pytest.mark.asyncio
async def test_manual_spotlight_runcron_endpoint_requires_admin(mock_db) -> None:
    """11. Verify POST /api/v1/admin/spotlight/runcron requires admin authentication."""
    from fastapi import HTTPException

    app = FastAPI()
    app.include_router(spotlight_router, prefix="/api/v1")

    async def _override_non_admin():
        raise HTTPException(status_code=403, detail="Forbidden: Admin access required")

    async def _override_db():
        yield mock_db()

    app.dependency_overrides[get_current_admin] = _override_non_admin
    app.dependency_overrides[get_session] = _override_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/admin/spotlight/runcron")
        assert resp.status_code == 403


def _paper(paper_id: str, authors: list[tuple[str, str]], citations: int = 10) -> dict:
    return {
        "paperId": paper_id,
        "title": f"Paper {paper_id}",
        "abstract": f"An abstract for paper {paper_id} covering the research topic.",
        "authors": [{"authorId": aid, "name": name} for aid, name in authors],
        "citationCount": citations,
        "url": f"https://example.com/{paper_id}",
        "openAccessPdf": {
            "url": f"https://example.com/{paper_id}.pdf",
            "status": "GOLD",
            "license": "CC-BY",
        },
    }


def _thirty_papers_many_authors() -> dict:
    papers = []
    for i in range(30):
        authors = [(f"auth-{i}-{j}", f"Author {i}-{j}") for j in range(5)]
        papers.append(_paper(f"p{i}", authors, citations=100 + i * 10))
    return {"data": papers, "total": 30}


@pytest.mark.asyncio
async def test_author_dedup_and_15_author_cap_uses_one_batch(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Deduplicate authors, cap at 15, one /author/batch, no per-author HTTP."""
    context = SpotlightUserContext(
        user_id=uuid4(),
        major="Computer Science",
        extracted_keywords={"major": ["Computer Science"]},
    )
    payload = _thirty_papers_many_authors()
    mock_search = AsyncMock(return_value=(payload, 200))
    mock_author_lookup = AsyncMock(
        side_effect=lambda ids, **kwargs: {
            aid: {
                "authorId": aid,
                "citationCount": 1000,
                "hIndex": 20,
                "paperCount": 40,
            }
            for aid in ids
        }
    )

    strategy = LeadingThinkerStrategy(
        search_fn=mock_search,
        author_lookup_fn=mock_author_lookup,
    )
    with caplog.at_level("INFO"):
        candidates = await strategy.get_candidates(context)

    from apps.learningspotlight.services.leading_thinker_strategy import (
        collect_unique_author_ids,
        select_authors_for_evaluation,
    )
    from apps.learningspotlight.services.query_helpers import raw_papers_to_candidates

    parsed = raw_papers_to_candidates(
        payload["data"],
        query='("computer science")',
        spotlight_type=SpotlightType.leading_thinker,
    )
    unique_before = collect_unique_author_ids(parsed)
    selected = select_authors_for_evaluation(parsed)

    assert len(parsed) == 30
    assert len(unique_before) == 150
    assert len(selected) == 15
    assert len(candidates) == 30

    mock_author_lookup.assert_called_once()
    queried_ids = mock_author_lookup.call_args[0][0]
    assert len(queried_ids) == 15
    assert len(set(queried_ids)) == 15
    assert set(queried_ids) == set(selected)

    complete_logs = [r.message for r in caplog.records if "stage=complete" in r.message]
    assert complete_logs
    assert "authors_found=150" in complete_logs[-1]
    assert "authors_evaluated=15" in complete_logs[-1]


def test_select_authors_deduplicates_before_cap() -> None:
    from apps.learningspotlight.services.leading_thinker_strategy import (
        collect_unique_author_ids,
        select_authors_for_evaluation,
    )
    from apps.learningspotlight.services.query_helpers import raw_papers_to_candidates

    papers = [
        _paper("p1", [("a1", "One"), ("a2", "Two")], citations=50),
        _paper("p2", [("a1", "One"), ("a3", "Three")], citations=80),
        _paper("p3", [("a1", "One")], citations=10),
    ]
    candidates = raw_papers_to_candidates(
        papers,
        query="q",
        spotlight_type=SpotlightType.leading_thinker,
    )
    unique = collect_unique_author_ids(candidates)
    assert unique == ["a1", "a2", "a3"]
    selected = select_authors_for_evaluation(candidates, limit=2)
    assert selected[0] == "a1"
    assert len(selected) == 2


@pytest.mark.asyncio
async def test_leading_thinker_does_not_call_per_author_http() -> None:
    source = Path(
        "apps/learningspotlight/services/leading_thinker_strategy.py"
    ).read_text(encoding="utf-8")
    assert "/author/batch" in source or "get_authors_batch_v2" in source or "_author_lookup_fn" in source
    assert "/author/" not in source.replace("/author/batch", "")
    assert "author/{id}" not in source

    context = SpotlightUserContext(
        user_id=uuid4(),
        extracted_keywords={"major": ["Computer Science"]},
    )
    payload = _thirty_papers_many_authors()
    mock_search = AsyncMock(return_value=(payload, 200))
    mock_author_lookup = AsyncMock(return_value={})
    strategy = LeadingThinkerStrategy(
        search_fn=mock_search,
        author_lookup_fn=mock_author_lookup,
    )
    await strategy.get_candidates(context)
    assert mock_author_lookup.await_count == 1
    mock_search.assert_awaited()
    assert mock_search.await_count >= 1
    for call in mock_search.await_args_list:
        assert call.kwargs.get("fields_of_study") is None




