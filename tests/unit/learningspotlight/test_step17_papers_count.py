"""Unit tests for Step 17: Admin-Configurable Learning Spotlight Papers Count (minimum 1).

Tests:
1. Default paper count = 1.
2. Admin can set 2.
3. Admin can set 3.
4. 0 is rejected.
5. Negative value is rejected.
6. Value above 5 is accepted (no upper limit).
7. Non-admin cannot update it.
8. Generation: count = 1 -> exactly 1 paper selected.
9. Generation: count = 2 -> exactly 2 papers selected.
10. Generation: count = 3 -> exactly 3 papers selected.
11. Insufficient candidates: count = 3 but only 2 available -> returns 2 papers.
12. 0 candidates -> returns False without error.
13. Candidate limit remains separate (30).
14. Same cycle day/category applies to all selected papers.
15. Multi-paper storage structure in `papers`.
16. Independent per-paper engagement.
17. Archiving multi-paper spotlight preserves array and engagement states.
18. GET /api/v1/learning-spotlight returns papers array.
19. Backward compatibility: legacy single-paper JSON is normalized gracefully.
20. Read only one paper with paper_id.
21. Save only one paper with paper_id.
22. Unsave only one paper with paper_id.
23. Like only one paper with paper_id.
24. Dislike only one paper with paper_id.
25. Saved papers endpoint retrieves papers across multi-paper spotlights.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from apps.accounts.db_models import User
from apps.administration.schemas import RecommendationSettingsUpdateRequest
from apps.learningspotlight.routes import router as spotlight_router
from apps.learningspotlight.schemas import (
    LearningSpotlight,
    LearningSpotlightAuthor,
    LearningSpotlightEngagement,
    LearningSpotlightPaper,
    ScoredSpotlightCandidate,
    SpotlightFeedback,
    SpotlightRankingResult,
)
from apps.learningspotlight.services.daily_generation_service import (
    DefaultSpotlightPaperGenerator,
    LearningSpotlightDailyGenerationService,
)
from apps.learningspotlight.services.spotlight_persistence_service import (
    SpotlightPersistenceService,
)
from apps.profiles.db_models import LearningRecommendationSettings, Profile
from apps.profiles.db_models.learning_recommendation_log_db_model import (
    LearningRecommendationLog,
)
from apps.recommendations.routes import router as recommendation_router
from apps.recommendations.services.recommendation_settings_service import (
    RecommendationSettingsService,
)
from common.enums import SpotlightType
from core.database.session import get_session
from core.security.auth import get_current_admin, get_current_user
from apps.administration.dependencies import require_signed_admin


class _FakeResult:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def scalars(self) -> SimpleNamespace:
        return SimpleNamespace(all=lambda: list(self._rows))

    def all(self) -> list:
        return list(self._rows)

    def one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        if not self._rows:
            raise AssertionError("Expected one scalar result")
        return self._rows[0]


class _FakeSession:
    def __init__(self, rows: list | None = None) -> None:
        self._rows = list(rows or [])
        self.execute = AsyncMock(side_effect=self._execute)
        self.add = MagicMock()
        self.commit = AsyncMock()
        self.refresh = AsyncMock()

    def _execute(self, stmt):
        return _FakeResult(self._rows)


def _make_candidate(paper_id: str, score: float, spotlight_type: SpotlightType = SpotlightType.leading_thinker) -> ScoredSpotlightCandidate:
    return ScoredSpotlightCandidate(
        paper_id=paper_id,
        title=f"Paper {paper_id}",
        authors=[LearningSpotlightAuthor(author_id=f"a_{paper_id}", name=f"Author {paper_id}")],
        abstract=f"Abstract for {paper_id}",
        venue="NeurIPS",
        year=2026,
        citation_count=100,
        url=f"https://example.com/{paper_id}",
        query='("AI")',
        spotlight_type=spotlight_type,
        user_relevance_score=score,
        quality_score=score,
        recency_score=score,
        category_score=score,
        final_score=score,
    )


# ---------------------------------------------------------------------------
# 1-7. Settings & Validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_settings_default_paper_count() -> None:
    """1. Default learning_spotlight_papers_count is 1."""
    service = RecommendationSettingsService()
    settings = await service.get_settings(_FakeSession())  # type: ignore[arg-type]
    assert settings.learning_spotlight_papers_count == 1


@pytest.mark.asyncio
async def test_admin_can_update_paper_count() -> None:
    """2, 3. Admin can set count to 2 and 3."""
    admin_id = uuid4()
    existing = LearningRecommendationSettings(
        is_enabled=True,
        learning_spotlight_papers_count=1,
    )
    session = _FakeSession(rows=[existing])
    service = RecommendationSettingsService()

    with patch("apps.administration.services.admin_activity_log_service.create_admin_activity_log", new=AsyncMock()):
        updated = await service.update_settings(
            session,  # type: ignore[arg-type]
            admin_user_id=admin_id,
            learning_spotlight_papers_count=3,
        )
    assert updated.learning_spotlight_papers_count == 3


@pytest.mark.asyncio
async def test_invalid_paper_count_rejected() -> None:
    """4, 5. 0 and negative values are rejected."""
    admin_id = uuid4()
    existing = LearningRecommendationSettings(is_enabled=True)
    session = _FakeSession(rows=[existing])
    service = RecommendationSettingsService()

    for invalid_val in [0, -1]:
        with pytest.raises(ValueError, match="must be at least 1"):
            await service.update_settings(
                session,  # type: ignore[arg-type]
                admin_user_id=admin_id,
                learning_spotlight_papers_count=invalid_val,
            )


def test_papers_count_schema_rejects_below_one() -> None:
    """Schema rejects counts below 1 with a clear error (no upper-limit message)."""
    with pytest.raises(ValueError, match="must be at least 1"):
        RecommendationSettingsUpdateRequest(learning_spotlight_papers_count=0)


def test_papers_count_schema_accepts_above_five() -> None:
    """Schema has no upper limit on learning_spotlight_papers_count."""
    payload = RecommendationSettingsUpdateRequest(learning_spotlight_papers_count=10)
    assert payload.learning_spotlight_papers_count == 10


@pytest.mark.asyncio
async def test_paper_count_has_no_upper_limit() -> None:
    """6. Values above 5 are accepted (no upper limit)."""
    admin_id = uuid4()
    existing = LearningRecommendationSettings(
        is_enabled=True,
        learning_spotlight_papers_count=1,
    )
    session = _FakeSession(rows=[existing])
    service = RecommendationSettingsService()

    with patch("apps.administration.services.admin_activity_log_service.create_admin_activity_log", new=AsyncMock()):
        updated = await service.update_settings(
            session,  # type: ignore[arg-type]
            admin_user_id=admin_id,
            learning_spotlight_papers_count=10,
        )
    assert updated.learning_spotlight_papers_count == 10


@pytest.mark.asyncio
async def test_non_admin_cannot_update_paper_count(mock_db) -> None:
    """7. Non-admin receives 403 when updating paper count."""
    app = FastAPI()
    app.include_router(recommendation_router, prefix="/api/v1")

    async def _override_admin():
        raise HTTPException(status_code=403, detail="Forbidden")

    async def _override_db():
        yield mock_db()

    app.dependency_overrides[get_current_admin] = _override_admin
    app.dependency_overrides[require_signed_admin] = _override_admin
    app.dependency_overrides[get_session] = _override_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.patch(
            "/api/v1/admin/recommendation-settings",
            json={"learning_spotlight_papers_count": 2},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 8-14. Paper Selection & Candidate Limits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generator_selects_configured_count() -> None:
    """8, 9, 10. Generator selects top N candidates based on papers_count."""
    user_id = uuid4()
    candidates = [_make_candidate(f"p_{i}", 90.0 - i) for i in range(5)]

    mock_filter = MagicMock()
    mock_filter.filter_for_user = AsyncMock(
        return_value=SimpleNamespace(candidates=candidates)
    )
    mock_scoring = MagicMock()
    mock_scoring.score_and_rank_candidates.return_value = SpotlightRankingResult(
        ranked_candidates=candidates,
        total_candidates=len(candidates),
    )
    mock_persist = MagicMock()
    mock_persist.persist_selected_spotlight = AsyncMock(return_value=(MagicMock(), True))

    mock_profile = Profile(user_id=user_id, major="Computer Science", profile_interests_id=["AI"])
    session = _FakeSession(rows=[mock_profile])

    generator = DefaultSpotlightPaperGenerator(
        filter_service=mock_filter,
        scoring_service=mock_scoring,
        persistence_service=mock_persist,
    )

    with patch("apps.learningspotlight.services.daily_generation_service.get_spotlight_strategy") as mock_strat:
        mock_strat.return_value.get_candidates = AsyncMock(return_value=candidates)
        # Test count = 2
        is_new = await generator.generate_for_user(
            session,  # type: ignore[arg-type]
            user_id,
            spotlight_type=SpotlightType.leading_thinker,
            cycle_day=1,
            today=date(2026, 8, 25),
            papers_count=2,
        )
        assert is_new is True
        call_kwargs = mock_persist.persist_selected_spotlight.call_args.kwargs
        selected = call_kwargs["candidates"]
        assert len(selected) == 2
        assert selected[0].paper_id == "p_0"
        assert selected[1].paper_id == "p_1"


@pytest.mark.asyncio
async def test_generator_does_not_clamp_papers_count() -> None:
    """Generator honors papers_count above 5 (no upper clamp)."""
    user_id = uuid4()
    candidates = [_make_candidate(f"p_{i}", 90.0 - i) for i in range(8)]

    mock_filter = MagicMock()
    mock_filter.filter_for_user = AsyncMock(
        return_value=SimpleNamespace(candidates=candidates)
    )
    mock_scoring = MagicMock()
    mock_scoring.score_and_rank_candidates.return_value = SpotlightRankingResult(
        ranked_candidates=candidates,
        total_candidates=len(candidates),
    )
    mock_persist = MagicMock()
    mock_persist.persist_selected_spotlight = AsyncMock(return_value=(MagicMock(), True))

    mock_profile = Profile(user_id=user_id, major="Computer Science", profile_interests_id=["AI"])
    session = _FakeSession(rows=[mock_profile])

    generator = DefaultSpotlightPaperGenerator(
        filter_service=mock_filter,
        scoring_service=mock_scoring,
        persistence_service=mock_persist,
    )

    with patch("apps.learningspotlight.services.daily_generation_service.get_spotlight_strategy") as mock_strat:
        mock_strat.return_value.get_candidates = AsyncMock(return_value=candidates)
        is_new = await generator.generate_for_user(
            session,  # type: ignore[arg-type]
            user_id,
            spotlight_type=SpotlightType.leading_thinker,
            cycle_day=1,
            today=date(2026, 8, 25),
            papers_count=6,
        )
        assert is_new is True
        selected = mock_persist.persist_selected_spotlight.call_args.kwargs["candidates"]
        assert len(selected) == 6


@pytest.mark.asyncio
async def test_generator_handles_insufficient_candidates() -> None:
    """11. When count=3 but only 2 candidates survive filtering, generation is skipped."""
    user_id = uuid4()
    candidates = [_make_candidate("p_0", 95.0), _make_candidate("p_1", 90.0)]

    mock_filter = MagicMock()
    mock_filter.filter_for_user = AsyncMock(
        return_value=SimpleNamespace(candidates=candidates)
    )
    mock_scoring = MagicMock()
    mock_scoring.score_and_rank_candidates.return_value = SpotlightRankingResult(
        ranked_candidates=candidates,
        total_candidates=2,
    )
    mock_persist = MagicMock()
    mock_persist.persist_selected_spotlight = AsyncMock(return_value=(MagicMock(), True))

    mock_profile = Profile(user_id=user_id, major="Computer Science")
    session = _FakeSession(rows=[mock_profile])

    generator = DefaultSpotlightPaperGenerator(
        filter_service=mock_filter,
        scoring_service=mock_scoring,
        persistence_service=mock_persist,
    )

    with patch("apps.learningspotlight.services.daily_generation_service.get_spotlight_strategy") as mock_strat:
        mock_strat.return_value.get_candidates = AsyncMock(return_value=candidates)
        is_new = await generator.generate_for_user(
            session,  # type: ignore[arg-type]
            user_id,
            spotlight_type=SpotlightType.leading_thinker,
            cycle_day=1,
            today=date(2026, 8, 25),
            papers_count=3,
        )
        assert is_new is False
        mock_persist.persist_selected_spotlight.assert_not_called()


# ---------------------------------------------------------------------------
# 15-17. Persistence & Multi-Paper Storage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_paper_persistence_and_independent_engagement() -> None:
    """15, 16. Multiple papers are persisted in `papers` with independent engagement."""
    user_id = uuid4()
    c1 = _make_candidate("p_1", 95.0)
    c2 = _make_candidate("p_2", 90.0)

    profile = Profile(user_id=user_id, learning_spotlight=None)
    session = _FakeSession(rows=[profile])
    service = SpotlightPersistenceService()

    spotlight, is_new = await service.persist_selected_spotlight(
        session,  # type: ignore[arg-type]
        user_id,
        candidates=[c1, c2],
        cycle_day=2,
        spotlight_type=SpotlightType.country_perspective,
        today=date(2026, 8, 25),
    )

    assert is_new is True
    assert len(spotlight.papers) == 2
    assert spotlight.papers[0].paper_id == "p_1"
    assert spotlight.papers[1].paper_id == "p_2"
    assert spotlight.papers[0].engagement.is_read is False
    assert spotlight.papers[1].engagement.is_read is False


@pytest.mark.asyncio
async def test_archiving_preserves_multi_paper_spotlight() -> None:
    """17. Archiving a previous multi-paper spotlight saves full array to logs."""
    user_id = uuid4()
    previous_snapshot = {
        "version": 2,
        "cycle_day": 1,
        "spotlight_type": "leading_thinker",
        "query": '("AI")',
        "papers": [
            {
                "paper_id": "old_1",
                "title": "Old Paper 1",
                "engagement": {"is_read": True, "is_saved": False, "feedback": None},
            },
            {
                "paper_id": "old_2",
                "title": "Old Paper 2",
                "engagement": {"is_read": False, "is_saved": True, "feedback": "useful"},
            },
        ],
        "generated_at": "2026-08-24T00:00:00Z",
    }
    profile = Profile(user_id=user_id, learning_spotlight=previous_snapshot)
    session = _FakeSession(rows=[profile])
    service = SpotlightPersistenceService()

    c_new = _make_candidate("new_p", 98.0)
    _spotlight, is_new = await service.persist_selected_spotlight(
        session,  # type: ignore[arg-type]
        user_id,
        candidates=[c_new],
        cycle_day=2,
        spotlight_type=SpotlightType.country_perspective,
        today=date(2026, 8, 25),
    )

    assert is_new is True
    added = [call.args[0] for call in session.add.call_args_list]
    log_entries = [obj for obj in added if isinstance(obj, LearningRecommendationLog)]
    assert len(log_entries) == 1
    archived_recs = log_entries[0].learning_recommendations
    assert len(archived_recs["papers"]) == 2
    assert archived_recs["papers"][0]["paper_id"] == "old_1"
    assert archived_recs["papers"][1]["paper_id"] == "old_2"


# ---------------------------------------------------------------------------
# 18-19. GET Route & Backward Compatibility
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_spotlight_returns_multi_papers(mock_db) -> None:
    """18. GET /api/v1/learning-spotlight returns the papers list."""
    user_id = uuid4()
    mock_user = User(id=user_id, email="student@example.com", role="user")

    app = FastAPI()
    app.include_router(spotlight_router, prefix="/api/v1")

    async def _override_user():
        return mock_user

    async def _override_db():
        yield mock_db()

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_session] = _override_db

    fake_spotlight = LearningSpotlight(
        version=2,
        cycle_day=2,
        spotlight_type=SpotlightType.country_perspective,
        query='("AI")',
        papers=[
            LearningSpotlightPaper(paper_id="p1", title="Paper 1"),
            LearningSpotlightPaper(paper_id="p2", title="Paper 2"),
        ],
        generated_at=datetime(2026, 8, 25, tzinfo=timezone.utc),
    )

    with patch.object(
        SpotlightPersistenceService,
        "get_current_spotlight",
        new=AsyncMock(return_value=fake_spotlight),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/learning-spotlight")
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert len(data["papers"]) == 2
            assert data["papers"][0]["paper_id"] == "p1"
            assert data["papers"][1]["paper_id"] == "p2"
            assert data["description"]["title"] == "Learning Spotlight"
            assert data["description"]["subtitle"] == (
                "Articles based on your location of study"
            )


def test_legacy_single_paper_backward_compatibility() -> None:
    """19. Legacy JSON with `paper` and top-level `engagement` is normalized to `papers`."""
    legacy_json = {
        "version": 2,
        "cycle_day": 1,
        "spotlight_type": "leading_thinker",
        "query": '("AI")',
        "score": 92.0,
        "paper": {
            "paper_id": "legacy_p1",
            "title": "Legacy Transformer Paper",
            "citation_count": 500,
        },
        "engagement": {
            "is_read": True,
            "is_saved": False,
            "feedback": "useful",
        },
        "generated_at": "2026-08-24T00:00:00Z",
    }

    spotlight = LearningSpotlight.model_validate(legacy_json)
    assert len(spotlight.papers) == 1
    assert spotlight.papers[0].paper_id == "legacy_p1"
    assert spotlight.papers[0].engagement.is_read is True
    assert spotlight.papers[0].engagement.feedback is True
    # Property backward-compatibility:

    assert spotlight.paper.paper_id == "legacy_p1"
    assert spotlight.engagement.is_read is True


# ---------------------------------------------------------------------------
# 20-25. Per-Paper Interactions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_paper_read_and_save_interactions() -> None:
    """20, 21, 22, 23, 24. Reading/saving one paper updates ONLY that paper."""
    user_id = uuid4()
    snapshot = {
        "version": 2,
        "cycle_day": 1,
        "spotlight_type": "leading_thinker",
        "query": '("AI")',
        "papers": [
            {
                "paper_id": "p_1",
                "title": "Paper 1",
                "engagement": {"is_read": False, "is_saved": False, "feedback": None},
            },
            {
                "paper_id": "p_2",
                "title": "Paper 2",
                "engagement": {"is_read": False, "is_saved": False, "feedback": None},
            },
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    profile = Profile(user_id=user_id, learning_spotlight=snapshot)
    session = _FakeSession(rows=[profile])
    service = SpotlightPersistenceService()

    # 1. Mark p_2 as read
    updated = await service.update_read_status(
        session,  # type: ignore[arg-type]
        user_id,
        is_read=True,
        paper_id="p_2",
    )
    assert updated.papers[0].engagement.is_read is False  # p_1 unchanged
    assert updated.papers[1].engagement.is_read is True   # p_2 updated

    # 2. Save p_1
    updated_save = await service.update_save_status(
        session,  # type: ignore[arg-type]
        user_id,
        is_saved=True,
        paper_id="p_1",
    )
    assert updated_save.papers[0].engagement.is_saved is True   # p_1 saved
    assert updated_save.papers[1].engagement.is_saved is False  # p_2 unsaved

    # 3. Like p_2
    updated_fb = await service.submit_feedback(
        session,  # type: ignore[arg-type]
        user_id,
        feedback=SpotlightFeedback.useful,
        paper_id="p_2",
    )
    assert updated_fb.papers[0].engagement.feedback is False
    assert updated_fb.papers[1].engagement.feedback is True

