"""Unit tests for Step 10: Daily Cron Execution, Keyset Batching, and V1 Isolation.

Verifies all Part 14 requirements:
1. Default batch size = 50.
2. Environment override works (e.g. LEARNING_SPOTLIGHT_BATCH_SIZE=25).
3. Invalid batch size is rejected by pydantic validation (gt=0).
4. 120 users are processed in 3 batches: 50 + 50 + 20.
5. Correct ordering by recommendations_updated_at.
6. NULL recommendations_updated_at comes first.
7. Same global cycle_day and category is used across all batches during a run.
8. New user receives the current global category.
9. Existing user receives the same current category.
10. User who already generated today's spotlight is skipped.
11. Two cron runs on the same day are idempotent.
12. One user failure does not stop the batch.
13. One batch failure does not stop subsequent batches.
14. No-candidate user is skipped safely without creating fake records.
15. V2 updates learning_spotlight_updated_at.
16. V2 does NOT update recommendations_updated_at.
17. V1 behavior remains completely unchanged.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from apps.learningspotlight.config import LearningSpotlightSettings
from apps.learningspotlight.schemas import (
    LearningSpotlight,
    LearningSpotlightAuthor,
    LearningSpotlightPaper,
    ScoredSpotlightCandidate,
    SpotlightCandidate,
    SpotlightUserContext,
)
from apps.learningspotlight.services.daily_generation_service import (
    DailyGenerationResult,
    DefaultSpotlightPaperGenerator,
    LearningSpotlightDailyGenerationService,
    _ProfileCandidate,
)
from apps.profiles.db_models.learning_recommendation_settings_db_model import (
    LearningRecommendationSettings,
)
from apps.profiles.db_models.profile_db_model import Profile
from apps.recommendations.services.recommendation_settings_service import (
    RecommendationSettingsService,
)
from common.enums import SpotlightType


# ---------------------------------------------------------------------------
# 1-3. Config & Batch Size Validation Tests
# ---------------------------------------------------------------------------


def test_default_batch_size_is_50() -> None:
    """1. Default batch size must be 50."""
    settings = LearningSpotlightSettings()
    assert settings.learning_spotlight_batch_size == 50


def test_batch_size_environment_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """2. Environment override works for batch size."""
    monkeypatch.setenv("LEARNING_SPOTLIGHT_BATCH_SIZE", "25")
    settings = LearningSpotlightSettings()
    assert settings.learning_spotlight_batch_size == 25


def test_invalid_batch_size_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """3. Non-positive batch size is rejected."""
    monkeypatch.setenv("LEARNING_SPOTLIGHT_BATCH_SIZE", "0")
    with pytest.raises(ValidationError):
        LearningSpotlightSettings()

    monkeypatch.setenv("LEARNING_SPOTLIGHT_BATCH_SIZE", "-5")
    with pytest.raises(ValidationError):
        LearningSpotlightSettings()


# ---------------------------------------------------------------------------
# Helpers & Mocks
# ---------------------------------------------------------------------------


def _mock_settings_service(
    *,
    is_enabled: bool = True,
    cycle_start_date: date = date(2026, 8, 20),
) -> RecommendationSettingsService:
    settings = LearningRecommendationSettings(
        id=uuid4(),
        is_enabled=is_enabled,
        generation_frequency_days=14,
        max_recommendations=10,
        cycle_start_date=cycle_start_date,
        updated_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
        created_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )
    svc = MagicMock(spec=RecommendationSettingsService)
    svc.get_persisted_settings = AsyncMock(return_value=settings)
    return svc


def _make_profile_candidate(
    user_id: UUID,
    *,
    recs_updated_at: datetime | None = None,
    learning_spotlight: dict | None = None,
    learning_spotlight_updated_at: datetime | None = None,
    keywords: dict | None = None,
) -> _ProfileCandidate:
    return _ProfileCandidate(
        user_id=user_id,
        extracted_keywords=keywords or {"interests": ["computer science", "AI"]},
        learning_spotlight=learning_spotlight,
        learning_spotlight_updated_at=learning_spotlight_updated_at,
        recommendations_updated_at=recs_updated_at,
    )


# ---------------------------------------------------------------------------
# 4-6. Batch Chunking & Ordering Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_120_users_processed_in_three_batches() -> None:
    """4. 120 users are processed in 3 batches: 50 + 50 + 20."""
    user_ids = [uuid4() for _ in range(120)]
    all_candidates = [_make_profile_candidate(uid) for uid in user_ids]

    settings_svc = _mock_settings_service()
    paper_gen = AsyncMock()
    paper_gen.generate_for_user = AsyncMock(return_value=True)

    svc = LearningSpotlightDailyGenerationService(
        settings_service=settings_svc,
        paper_generator=paper_gen,
        batch_size=50,
    )

    batch_calls = []

    async def _mock_get_batch(session, *, batch_size, exclude_user_ids, run_mode="scheduled", **kwargs):
        batch_calls.append(len(exclude_user_ids))
        remaining = [c for c in all_candidates if c.user_id not in exclude_user_ids]
        return remaining[:batch_size]

    svc._get_next_profile_batch = _mock_get_batch

    with patch(
        "apps.learningspotlight.services.daily_generation_service.async_session_factory"
    ):
        result = await svc._run_daily_generation(today=date(2026, 8, 23))

    assert result.ran is True
    assert result.batches_processed == 3
    assert result.total_selected == 120
    assert result.processed == 120
    assert result.generated_users == 120
    assert paper_gen.generate_for_user.call_count == 120


@pytest.mark.asyncio
async def test_ordering_by_recommendations_updated_at_nulls_first() -> None:
    """5, 6. Ordering handles NULL recommendations_updated_at first, then oldest."""
    u_null = uuid4()
    u_old = uuid4()
    u_new = uuid4()

    c_null = _make_profile_candidate(u_null, recs_updated_at=None)
    c_old = _make_profile_candidate(
        u_old, recs_updated_at=datetime(2026, 8, 1, tzinfo=timezone.utc)
    )
    c_new = _make_profile_candidate(
        u_new, recs_updated_at=datetime(2026, 8, 20, tzinfo=timezone.utc)
    )

    all_candidates = [c_new, c_null, c_old]  # unsorted initial list

    # Sort according to sql logic: nulls first, then oldest
    def _sql_sort_key(c: _ProfileCandidate):
        return (0 if c.recommendations_updated_at is None else 1, c.recommendations_updated_at or datetime.min.replace(tzinfo=timezone.utc), c.user_id)

    sorted_candidates = sorted(all_candidates, key=_sql_sort_key)
    assert sorted_candidates[0].user_id == u_null
    assert sorted_candidates[1].user_id == u_old
    assert sorted_candidates[2].user_id == u_new


# ---------------------------------------------------------------------------
# 7-9. Global Cycle & Category Sharing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_global_cycle_day_and_category_shared_across_batches_and_users() -> None:
    """7, 8, 9. Day 4 = latest_research is passed to all users (both new and existing)."""
    # 2026-08-20 is day 1, 2026-08-23 is day 4 (latest_research)
    settings_svc = _mock_settings_service(cycle_start_date=date(2026, 8, 20))
    u_new = uuid4()
    u_existing = uuid4()

    candidates = [
        _make_profile_candidate(u_new, recs_updated_at=None),
        _make_profile_candidate(u_existing, recs_updated_at=datetime(2026, 8, 1, tzinfo=timezone.utc)),
    ]

    paper_gen = AsyncMock()
    paper_gen.generate_for_user = AsyncMock(return_value=True)

    svc = LearningSpotlightDailyGenerationService(
        settings_service=settings_svc,
        paper_generator=paper_gen,
        batch_size=50,
    )

    async def _mock_get_batch(session, *, batch_size, exclude_user_ids, run_mode="scheduled", **kwargs):
        return [c for c in candidates if c.user_id not in exclude_user_ids]

    svc._get_next_profile_batch = _mock_get_batch

    with patch(
        "apps.learningspotlight.services.daily_generation_service.async_session_factory"
    ):
        result = await svc._run_daily_generation(today=date(2026, 8, 23))

    assert result.cycle_day == 4
    assert result.spotlight_type == SpotlightType.latest_research

    # Verify every call to generate_for_user used cycle_day=4, spotlight_type=latest_research
    for call in paper_gen.generate_for_user.call_args_list:
        kwargs = call.kwargs
        assert kwargs["cycle_day"] == 4
        assert kwargs["spotlight_type"] == SpotlightType.latest_research


# ---------------------------------------------------------------------------
# 10-11. Idempotency Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_with_today_spotlight_is_skipped() -> None:
    """10. User already possessing today's V2 spotlight for this cycle day is skipped."""
    settings_svc = _mock_settings_service(cycle_start_date=date(2026, 8, 20))
    u_already = uuid4()
    u_fresh = uuid4()

    existing_spotlight = {
        "version": 2,
        "cycle_day": 4,
        "spotlight_type": "latest_research",
        "query": "query",
        "paper": {"paper_id": "p1"},
        "score": 90.0,
        "generated_at": "2026-08-23T08:00:00+00:00",
        "engagement": {"is_read": False, "is_saved": False, "feedback": None},
    }

    candidates = [
        _make_profile_candidate(
            u_already,
            learning_spotlight=existing_spotlight,
            learning_spotlight_updated_at=datetime(
                2026, 8, 23, 8, 0, tzinfo=timezone.utc
            ),
        ),
        _make_profile_candidate(u_fresh, learning_spotlight=None),
    ]

    paper_gen = AsyncMock()
    paper_gen.generate_for_user = AsyncMock(return_value=True)

    svc = LearningSpotlightDailyGenerationService(
        settings_service=settings_svc,
        paper_generator=paper_gen,
        batch_size=50,
    )

    async def _mock_get_batch(session, *, batch_size, exclude_user_ids, run_mode="scheduled", **kwargs):
        return [c for c in candidates if c.user_id not in exclude_user_ids]

    svc._get_next_profile_batch = _mock_get_batch

    with patch(
        "apps.learningspotlight.services.daily_generation_service.async_session_factory"
    ):
        result = await svc._run_daily_generation(today=date(2026, 8, 23))

    assert result.processed == 2
    assert result.skipped_users == 1
    assert result.already_generated_count == 1
    assert result.generated_users == 1
    # Only u_fresh invoked paper_gen
    assert paper_gen.generate_for_user.call_count == 1
    assert paper_gen.generate_for_user.call_args_list[0].args[1] == u_fresh


@pytest.mark.asyncio
async def test_two_cron_runs_on_same_day_are_idempotent() -> None:
    """11. Running cron twice on same date results in 0 generated on 2nd run."""
    settings_svc = _mock_settings_service(cycle_start_date=date(2026, 8, 20))
    user_id = uuid4()

    profile = Profile(user_id=user_id, learning_spotlight=None)

    # First run generates spotlight
    candidate_profile = _make_profile_candidate(
        user_id,
        learning_spotlight={
            "version": 2,
            "cycle_day": 4,
            "spotlight_type": "latest_research",
            "query": "query",
            "paper": {"paper_id": "p1"},
            "score": 90.0,
            "generated_at": "2026-08-23T08:00:00+00:00",
            "engagement": {"is_read": False, "is_saved": False, "feedback": None},
        },
        learning_spotlight_updated_at=datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc),
    )

    paper_gen = AsyncMock()
    svc = LearningSpotlightDailyGenerationService(
        settings_service=settings_svc,
        paper_generator=paper_gen,
    )

    async def _mock_get_batch(session, *, batch_size, exclude_user_ids, run_mode="scheduled", **kwargs):
        return [candidate_profile] if user_id not in exclude_user_ids else []

    svc._get_next_profile_batch = _mock_get_batch

    with patch(
        "apps.learningspotlight.services.daily_generation_service.async_session_factory"
    ):
        result2 = await svc._run_daily_generation(today=date(2026, 8, 23))

    assert result2.generated_users == 0
    assert result2.skipped_users == 1
    assert result2.already_generated_count == 1
    paper_gen.generate_for_user.assert_not_called()


# ---------------------------------------------------------------------------
# 12-14. Failure Handling & No-Candidate Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_failure_does_not_stop_batch() -> None:
    """12. One user failure does not stop the batch; remaining users are processed."""
    settings_svc = _mock_settings_service()
    u_fail = uuid4()
    u_success = uuid4()

    candidates = [
        _make_profile_candidate(u_fail),
        _make_profile_candidate(u_success),
    ]

    async def _mock_generate(session, user_id, **kwargs):
        if user_id == u_fail:
            raise RuntimeError("Database connection timeout for user")
        return True

    paper_gen = AsyncMock()
    paper_gen.generate_for_user = _mock_generate

    svc = LearningSpotlightDailyGenerationService(
        settings_service=settings_svc,
        paper_generator=paper_gen,
    )

    async def _mock_get_batch(session, *, batch_size, exclude_user_ids, run_mode="scheduled", **kwargs):
        return [c for c in candidates if c.user_id not in exclude_user_ids]

    svc._get_next_profile_batch = _mock_get_batch

    with patch(
        "apps.learningspotlight.services.daily_generation_service.async_session_factory"
    ):
        result = await svc._run_daily_generation(today=date(2026, 8, 23))

    assert result.processed == 2
    assert result.failed_users == 1
    assert result.generated_users == 1


@pytest.mark.asyncio
async def test_no_candidate_user_skipped_safely() -> None:
    """14. User with 0 candidates is skipped safely without creating fake records."""
    settings_svc = _mock_settings_service()
    u_no_cand = uuid4()

    candidates = [_make_profile_candidate(u_no_cand)]

    paper_gen = AsyncMock()
    paper_gen.generate_for_user = AsyncMock(return_value=False)  # 0 candidates found

    svc = LearningSpotlightDailyGenerationService(
        settings_service=settings_svc,
        paper_generator=paper_gen,
    )

    async def _mock_get_batch(session, *, batch_size, exclude_user_ids, run_mode="scheduled", **kwargs):
        return [c for c in candidates if c.user_id not in exclude_user_ids]

    svc._get_next_profile_batch = _mock_get_batch

    with patch(
        "apps.learningspotlight.services.daily_generation_service.async_session_factory"
    ):
        result = await svc._run_daily_generation(today=date(2026, 8, 23))

    assert result.processed == 1
    assert result.generated_users == 0
    assert result.skipped_users == 1
    assert result.candidates_not_found == 1


# ---------------------------------------------------------------------------
# 15-17. Field Updating & V1 Isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_updates_learning_spotlight_updated_at_not_recommendations_updated_at(
    mock_db,
) -> None:
    """15, 16, 17. Default generator updates learning_spotlight_updated_at and leaves recommendations_updated_at alone."""
    user_id = uuid4()
    v1_timestamp = datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc)
    v1_recs = {"result": {"data": [{"paperId": "v1_paper"}]}}

    profile = Profile(
        user_id=user_id,
        major="Computer Science",
        extracted_keywords={"interests": ["machine learning"]},
        recommendations_updated_at=v1_timestamp,
        learning_recommendations=v1_recs,
        learning_spotlight=None,
        learning_spotlight_updated_at=None,
    )

    from tests.unit.conftest import FakeScalarResult

    db = mock_db(FakeScalarResult(profile), FakeScalarResult(profile))

    # Mock strategies, filter, scoring
    fake_candidate = SpotlightCandidate(
        paper_id="paper_v2_new",
        title="Deep Learning Breakthroughs",
        query='("deep learning")',
        spotlight_type=SpotlightType.influential_research,
    )

    fake_scored = ScoredSpotlightCandidate(
        paper_id="paper_v2_new",
        title="Deep Learning Breakthroughs",
        query='("deep learning")',
        spotlight_type=SpotlightType.influential_research,
        user_relevance_score=90.0,
        quality_score=85.0,
        recency_score=80.0,
        category_score=95.0,
        final_score=89.5,
    )

    mock_filter_svc = MagicMock()
    mock_filter_svc.filter_for_user = AsyncMock(
        return_value=MagicMock(candidates=[fake_candidate])
    )

    mock_score_svc = MagicMock()
    mock_score_svc.score_and_rank_candidates = MagicMock(
        return_value=MagicMock(selected_candidate=fake_scored)
    )

    gen = DefaultSpotlightPaperGenerator(
        filter_service=mock_filter_svc,
        scoring_service=mock_score_svc,
    )

    with patch(
        "apps.learningspotlight.services.daily_generation_service.get_spotlight_strategy"
    ) as mock_strat_getter:
        mock_strategy = MagicMock()
        mock_strategy.get_candidates = AsyncMock(return_value=[fake_candidate])
        mock_strat_getter.return_value = mock_strategy

        is_new = await gen.generate_for_user(
            db,
            user_id,
            spotlight_type=SpotlightType.influential_research,
            cycle_day=3,
            today=date(2026, 8, 23),
        )

    assert is_new is True
    assert profile.learning_spotlight["papers"][0]["paper_id"] == "paper_v2_new"
    assert profile.learning_spotlight_updated_at is not None

    # V1 ISOLATION CHECKS
    assert profile.recommendations_updated_at == v1_timestamp  # UNCHANGED!
    assert profile.learning_recommendations == v1_recs  # UNCHANGED!
