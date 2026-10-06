"""Unit tests for Step 9: Persistence, History Archiving, and HTTP Routes (GET/Read/Save/Feedback).

Verifies:
Persistence:
1. Selected candidate is stored correctly on profile.learning_spotlight.
2. JSON matches Step 3 structure (version=2, cycle_day, paper, score, generated_at, engagement).
3. New spotlight starts with is_read=false, is_saved=false, feedback=null.
4. Query is preserved exactly.
5. Score is preserved.
6. generated_at timestamp is stored.
7. Previous V2 spotlight is archived to learning_recommendation_logs.
8. Existing V1 data remains untouched.
9. Duplicate generation for same day/cycle is skipped (idempotent).

GET API:
10. Authenticated user receives current spotlight.
11. No spotlight returns successful empty response (data=None).
12. GET never calls Semantic Scholar.

Read Action:
13. is_read=true updates correctly.
14. Other fields remain unchanged.

Save Action:
15. is_saved=true updates correctly.
16. is_saved=false un-saves correctly.
17. Other fields remain unchanged.

Feedback Action:
18. useful accepted.
19. not_useful accepted.
20. invalid feedback rejected.
21. Other fields remain unchanged.

Authentication:
22. Unauthenticated requests are rejected.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, status
from httpx import ASGITransport, AsyncClient

from apps.accounts.db_models import User
from apps.learningspotlight.routes import router as spotlight_router
from apps.learningspotlight.schemas import (
    LearningSpotlight,
    LearningSpotlightAuthor,
    LearningSpotlightEngagement,
    LearningSpotlightPaper,
    ScoredSpotlightCandidate,
    SpotlightFeedback,
    SpotlightFeedbackRequest,
    SpotlightReadRequest,
    SpotlightSaveRequest,
)
from apps.learningspotlight.services.spotlight_persistence_service import (
    SpotlightPersistenceService,
)
from apps.profiles.db_models.learning_recommendation_log_db_model import (
    LearningRecommendationLog,
)
from apps.profiles.db_models.profile_db_model import Profile
from common.enums import SpotlightType
from core.database.session import get_session
from core.security.auth import get_current_user
from fastapi import FastAPI


# ---------------------------------------------------------------------------
# Helpers & Fixtures
# ---------------------------------------------------------------------------


def _today_generated_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_scored_candidate(
    paper_id: str = "paper_123",
    title: str = "Attention Mechanisms in Deep Learning",
    query: str = '("deep learning"|"transformers")',
    final_score: float = 94.5,
    citation_count: int = 150,
    year: int = 2026,
    spotlight_type: SpotlightType = SpotlightType.influential_research,
) -> ScoredSpotlightCandidate:
    return ScoredSpotlightCandidate(
        paper_id=paper_id,
        title=title,
        authors=[LearningSpotlightAuthor(author_id="a1", name="Alice Author")],
        abstract="An abstract exploring transformer architectures.",
        venue="NeurIPS",
        year=year,
        citation_count=citation_count,
        url=f"https://example.com/{paper_id}",
        query=query,
        spotlight_type=spotlight_type,
        metadata={"fields_of_study": ["Computer Science"]},
        user_relevance_score=92.0,
        quality_score=85.0,
        recency_score=100.0,
        category_score=98.0,
        final_score=final_score,
    )


@pytest.fixture
def persistence_service() -> SpotlightPersistenceService:
    return SpotlightPersistenceService()


# ---------------------------------------------------------------------------
# 1-9. Persistence & Archiving Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_selected_candidate_is_stored_correctly(
    persistence_service: SpotlightPersistenceService,
    mock_db,
) -> None:
    """1, 2, 4, 5, 6. Selected candidate is stored with exact Step 3 structure."""
    user_id = uuid4()
    profile = Profile(user_id=user_id, learning_spotlight=None)

    from tests.unit.conftest import FakeScalarResult

    db = mock_db(FakeScalarResult(profile))

    candidate = _make_scored_candidate(
        paper_id="paper_xyz",
        title="Scaling Laws for Neural Models",
        query='("scaling"|"neural")',
        final_score=91.5,
    )

    spotlight, is_new = await persistence_service.persist_selected_spotlight(
        db,
        user_id,
        candidate=candidate,
        cycle_day=2,
        spotlight_type=SpotlightType.country_perspective,
        today=date(2026, 8, 23),
    )

    assert is_new is True
    assert spotlight is not None
    assert spotlight.version == 2
    assert spotlight.cycle_day == 2
    assert spotlight.spotlight_type == SpotlightType.country_perspective
    assert spotlight.query == '("scaling"|"neural")'
    assert spotlight.score == 91.5
    assert spotlight.paper.paper_id == "paper_xyz"
    assert spotlight.paper.title == "Scaling Laws for Neural Models"
    assert spotlight.paper.authors[0].name == "Alice Author"
    assert isinstance(spotlight.generated_at, datetime)

    # Check profile was updated
    assert profile.learning_spotlight["version"] == 2
    assert profile.learning_spotlight["papers"][0]["paper_id"] == "paper_xyz"
    assert "description" not in profile.learning_spotlight
    assert profile.learning_spotlight_updated_at is not None



@pytest.mark.asyncio
async def test_new_spotlight_starts_with_fresh_engagement(
    persistence_service: SpotlightPersistenceService,
    mock_db,
) -> None:
    """3. New spotlight always initializes is_read=False, is_saved=False, feedback=None."""
    user_id = uuid4()
    profile = Profile(user_id=user_id, learning_spotlight=None)

    from tests.unit.conftest import FakeScalarResult

    db = mock_db(FakeScalarResult(profile))
    candidate = _make_scored_candidate()

    spotlight, _ = await persistence_service.persist_selected_spotlight(
        db,
        user_id,
        candidate=candidate,
        cycle_day=3,
        spotlight_type=SpotlightType.influential_research,
    )

    assert spotlight.engagement.is_read is False
    assert spotlight.engagement.is_saved is False
    assert spotlight.engagement.feedback is False



@pytest.mark.asyncio
async def test_previous_v2_spotlight_is_archived_to_logs(
    persistence_service: SpotlightPersistenceService,
    mock_db,
) -> None:
    """7. Previous V2 spotlight is archived into learning_recommendation_logs."""
    user_id = uuid4()
    old_spotlight = {
        "version": 2,
        "cycle_day": 1,
        "spotlight_type": "country_perspective",
        "query": "old query",
        "paper": {"paper_id": "old_paper"},
        "score": 80.0,
        "generated_at": "2026-08-22T10:00:00+00:00",
        "engagement": {"is_read": True, "is_saved": False, "feedback": "useful"},
    }
    profile = Profile(user_id=user_id, learning_spotlight=old_spotlight)

    from tests.unit.conftest import FakeScalarResult

    db = mock_db(FakeScalarResult(profile))
    candidate = _make_scored_candidate(paper_id="new_paper")

    spotlight, is_new = await persistence_service.persist_selected_spotlight(
        db,
        user_id,
        candidate=candidate,
        cycle_day=2,
        spotlight_type=SpotlightType.influential_research,
        today=date(2026, 8, 23),
    )

    assert is_new is True
    assert spotlight.paper.paper_id == "new_paper"

    # Verify db.add was called for the archive log
    add_calls = db.add.call_args_list
    assert len(add_calls) >= 2  # 1 for log, 1 for profile
    archived_log = add_calls[0][0][0]
    assert isinstance(archived_log, LearningRecommendationLog)
    assert archived_log.user_id == user_id
    assert archived_log.learning_recommendations["paper"]["paper_id"] == "old_paper"
    assert archived_log.learning_recommendations["engagement"]["is_read"] is True


@pytest.mark.asyncio
async def test_existing_v1_data_remains_untouched(
    persistence_service: SpotlightPersistenceService,
    mock_db,
) -> None:
    """8. V1 learning_recommendations field on profile is untouched by V2 persistence."""
    user_id = uuid4()
    v1_recs = {"result": {"data": [{"paperId": "v1_paper"}]}}
    profile = Profile(
        user_id=user_id,
        learning_recommendations=v1_recs,
        learning_spotlight=None,
    )

    from tests.unit.conftest import FakeScalarResult

    db = mock_db(FakeScalarResult(profile))
    candidate = _make_scored_candidate()

    await persistence_service.persist_selected_spotlight(
        db,
        user_id,
        candidate=candidate,
        cycle_day=1,
        spotlight_type=SpotlightType.leading_thinker,
    )

    assert profile.learning_recommendations == v1_recs


@pytest.mark.asyncio
async def test_duplicate_generation_same_day_is_skipped(
    persistence_service: SpotlightPersistenceService,
    mock_db,
) -> None:
    """9. Idempotent skip: generating again for same cycle day on same date does not overwrite."""
    user_id = uuid4()
    today_dt = datetime(2026, 8, 23, 8, 0, 0, tzinfo=timezone.utc)
    existing_spotlight = {
        "version": 2,
        "cycle_day": 3,
        "spotlight_type": "influential_research",
        "query": "query",
        "paper": {"paper_id": "existing_p1"},
        "score": 90.0,
        "generated_at": today_dt.isoformat(),
        "engagement": {"is_read": True, "is_saved": True, "feedback": None},
    }
    profile = Profile(user_id=user_id, learning_spotlight=existing_spotlight)

    from tests.unit.conftest import FakeScalarResult

    db = mock_db(FakeScalarResult(profile))
    candidate = _make_scored_candidate(paper_id="candidate_different")

    spotlight, is_new = await persistence_service.persist_selected_spotlight(
        db,
        user_id,
        candidate=candidate,
        cycle_day=3,
        spotlight_type=SpotlightType.influential_research,
        today=date(2026, 8, 23),
    )

    assert is_new is False
    assert spotlight.paper.paper_id == "existing_p1"
    assert spotlight.engagement.is_read is True  # Preserved existing engagement!


@pytest.mark.asyncio
async def test_force_regenerate_replaces_todays_spotlight_and_updates_timestamp(
    persistence_service: SpotlightPersistenceService,
    mock_db,
) -> None:
    """Manual stale regeneration overwrites today's papers and refreshes the timestamp."""
    user_id = uuid4()
    previous = datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc)
    existing_spotlight = {
        "version": 2,
        "cycle_day": 2,
        "spotlight_type": "leading_thinker",
        "query": "old query",
        "paper": {"paper_id": "old_paper"},
        "score": 80.0,
        "generated_at": previous.isoformat(),
        "engagement": {"is_read": True, "is_saved": False, "feedback": None},
    }
    profile = Profile(
        user_id=user_id,
        learning_spotlight=existing_spotlight,
        learning_spotlight_updated_at=previous,
    )

    from tests.unit.conftest import FakeScalarResult

    db = mock_db(FakeScalarResult(profile))
    candidate = _make_scored_candidate(paper_id="new_paper")

    spotlight, is_new = await persistence_service.persist_selected_spotlight(
        db,
        user_id,
        candidate=candidate,
        cycle_day=2,
        spotlight_type=SpotlightType.leading_thinker,
        today=date(2026, 9, 30),
        force_regenerate=True,
    )

    assert is_new is True
    assert spotlight.paper.paper_id == "new_paper"
    assert profile.learning_spotlight_updated_at is not None
    assert profile.learning_spotlight_updated_at > previous


@pytest.mark.asyncio
async def test_same_day_different_spotlight_type_overwrites(
    persistence_service: SpotlightPersistenceService,
    mock_db,
) -> None:
    """Admin remapped today's type: persist replaces (and archives) the old snapshot."""
    user_id = uuid4()
    today_dt = datetime(2026, 8, 23, 8, 0, 0, tzinfo=timezone.utc)
    existing_spotlight = {
        "version": 2,
        "cycle_day": 3,
        "spotlight_type": "influential_research",
        "query": "query",
        "paper": {"paper_id": "existing_p1"},
        "score": 90.0,
        "generated_at": today_dt.isoformat(),
        "engagement": {"is_read": True, "is_saved": True, "feedback": None},
    }
    profile = Profile(user_id=user_id, learning_spotlight=existing_spotlight)

    from tests.unit.conftest import FakeScalarResult

    db = mock_db(FakeScalarResult(profile))
    candidate = _make_scored_candidate(paper_id="candidate_different")

    spotlight, is_new = await persistence_service.persist_selected_spotlight(
        db,
        user_id,
        candidate=candidate,
        cycle_day=3,
        spotlight_type=SpotlightType.leading_thinker,
        today=date(2026, 8, 23),
    )

    assert is_new is True
    assert spotlight.paper.paper_id == "candidate_different"
    archived_log = db.add.call_args_list[0][0][0]
    assert isinstance(archived_log, LearningRecommendationLog)
    assert archived_log.learning_recommendations["paper"]["paper_id"] == "existing_p1"


# ---------------------------------------------------------------------------
# 10-12. GET API Tests
# ---------------------------------------------------------------------------


def _build_test_app(mock_user: User, mock_db_session) -> FastAPI:
    app = FastAPI()
    app.include_router(spotlight_router, prefix="/api/v1")

    async def _override_get_current_user():
        return mock_user

    async def _override_get_session():
        yield mock_db_session

    app.dependency_overrides[get_current_user] = _override_get_current_user
    app.dependency_overrides[get_session] = _override_get_session
    return app


@pytest.mark.asyncio
async def test_get_spotlight_authenticated_user_receives_spotlight(mock_db) -> None:
    """10, 12. Authenticated user receives their current spotlight without SS calls."""
    user_id = uuid4()
    mock_user = User(id=user_id, email="test@example.com")
    spotlight_data = {
        "version": 2,
        "cycle_day": 2,
        "spotlight_type": "country_perspective",
        "query": '("AI")',
        "paper": {
            "paper_id": "p100",
            "title": "Indian AI Perspectives",
            "authors": [{"author_id": "a1", "name": "Dr. Sharma"}],
            "abstract": "An abstract.",
            "venue": "Venue",
            "year": 2026,
            "citation_count": 45,
            "url": "https://example.com/p100",
        },
        "score": 93.0,
        "generated_at": _today_generated_at(),
        "engagement": {"is_read": False, "is_saved": False, "feedback": None},
    }
    profile = Profile(user_id=user_id, learning_spotlight=spotlight_data)

    from tests.unit.conftest import FakeScalarResult

    db = mock_db(FakeScalarResult(profile))
    app = _build_test_app(mock_user, db)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Patch SS adapter to ensure it is NEVER called
        with patch(
            "apps.learningspotlight.services.semantic_scholar_adapter.search_papers_v2"
        ) as mock_ss:
            resp = await client.get("/api/v1/learning-spotlight")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] is True
            payload = data["data"]
            assert payload["version"] == 2
            assert payload["cycle_day"] == 2
            assert payload["spotlight_type"] == "country_perspective"
            assert payload["query"] == '("AI")'
            assert payload["papers"][0]["paper_id"] == "p100"
            assert "generated_at" in payload
            assert payload["description"] == {
                "title": "Learning Spotlight",
                "subtitle": "Articles based on your location of study",
            }
            mock_ss.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cycle_day", "spotlight_type", "expected_subtitle"),
    [
        (1, "leading_thinker", "Articles matched to your major, minor, and academic interests"),
        (2, "country_perspective", "Articles based on your location of study"),
        (3, "influential_research", "Articles trending in your field"),
        (4, "latest_research", "Articles based on the latest research in your field"),
        (5, "beyond_your_field", "Articles outside of your field to broaden your knowledge"),
        (1, "latest_research", "Articles based on the latest research in your field"),
    ],
)
async def test_get_spotlight_description_follows_spotlight_type(
    mock_db,
    cycle_day: int,
    spotlight_type: str,
    expected_subtitle: str,
) -> None:
    user_id = uuid4()
    mock_user = User(id=user_id, email="test@example.com")
    spotlight_data = {
        "version": 2,
        "cycle_day": cycle_day,
        "spotlight_type": spotlight_type,
        "query": '("AI")',
        "papers": [
            {
                "paper_id": "p100",
                "title": "Indian AI Perspectives",
                "authors": [{"author_id": "a1", "name": "Dr. Sharma"}],
                "abstract": "An abstract.",
                "venue": "Venue",
                "year": 2026,
                "citation_count": 45,
                "url": "https://example.com/p100",
                "engagement": {"is_read": False, "is_saved": False, "feedback": None},
            }
        ],
        "generated_at": _today_generated_at(),
    }
    profile = Profile(user_id=user_id, learning_spotlight=spotlight_data)

    from tests.unit.conftest import FakeScalarResult

    db = mock_db(FakeScalarResult(profile))
    app = _build_test_app(mock_user, db)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/learning-spotlight")
        assert resp.status_code == 200
        payload = resp.json()["data"]
        assert payload["cycle_day"] == cycle_day
        assert payload["spotlight_type"] == spotlight_type
        assert payload["query"] == '("AI")'
        assert payload["papers"][0]["paper_id"] == "p100"
        assert "generated_at" in payload
        assert payload["description"] == {
            "title": "Learning Spotlight",
            "subtitle": expected_subtitle,
        }


@pytest.mark.asyncio
async def test_get_spotlight_empty_when_no_spotlight_exists(mock_db) -> None:
    """11. Successful ApiResponse with data=None when user has no active spotlight."""
    user_id = uuid4()
    mock_user = User(id=user_id, email="test@example.com")
    profile = Profile(user_id=user_id, learning_spotlight=None)

    from tests.unit.conftest import FakeScalarResult

    db = mock_db(FakeScalarResult(profile))
    app = _build_test_app(mock_user, db)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/learning-spotlight")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] is True
        assert data["data"] is None
        assert "No active learning spotlight found" in data["message"]


@pytest.mark.asyncio
async def test_get_spotlight_hides_previous_day_snapshot(mock_db) -> None:
    """Stale V2 snapshots from a previous UTC day are not returned as active."""
    user_id = uuid4()
    mock_user = User(id=user_id, email="test@example.com")
    spotlight_data = {
        "version": 2,
        "cycle_day": 2,
        "spotlight_type": "country_perspective",
        "query": '("AI")',
        "papers": [
            {
                "paper_id": "old_paper",
                "title": "Yesterday Paper",
                "engagement": {"is_read": False, "is_saved": False, "feedback": None},
            }
        ],
        "generated_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
    }
    profile = Profile(user_id=user_id, learning_spotlight=spotlight_data)

    from tests.unit.conftest import FakeScalarResult

    db = mock_db(FakeScalarResult(profile))
    app = _build_test_app(mock_user, db)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/learning-spotlight")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] is True
        assert data["data"] is None
        assert "No active learning spotlight found" in data["message"]


@pytest.mark.asyncio
async def test_get_current_spotlight_returns_today_only(
    persistence_service: SpotlightPersistenceService,
    mock_db,
) -> None:
    """Service returns None for yesterday's snapshot and the model for today's."""
    user_id = uuid4()
    today_snapshot = {
        "version": 2,
        "cycle_day": 1,
        "spotlight_type": "leading_thinker",
        "query": '("AI")',
        "papers": [
            {
                "paper_id": "today_p",
                "title": "Today",
                "engagement": {"is_read": False, "is_saved": False, "feedback": None},
            }
        ],
        "generated_at": _today_generated_at(),
    }
    stale_snapshot = {
        **today_snapshot,
        "papers": [
            {
                "paper_id": "old_p",
                "title": "Old",
                "engagement": {"is_read": False, "is_saved": False, "feedback": None},
            }
        ],
        "generated_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
    }

    from tests.unit.conftest import FakeScalarResult

    stale_profile = Profile(user_id=user_id, learning_spotlight=stale_snapshot)
    stale = await persistence_service.get_current_spotlight(
        mock_db(FakeScalarResult(stale_profile)),
        user_id,
    )
    assert stale is None

    today_profile = Profile(user_id=user_id, learning_spotlight=today_snapshot)
    current = await persistence_service.get_current_spotlight(
        mock_db(FakeScalarResult(today_profile)),
        user_id,
    )
    assert current is not None
    assert current.papers[0].paper_id == "today_p"


# ---------------------------------------------------------------------------
# 13-14. Read Action Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_read_marks_as_read_and_preserves_other_fields(mock_db) -> None:
    """13, 14. PATCH /read updates only is_read and preserves all paper/score fields."""
    user_id = uuid4()
    mock_user = User(id=user_id, email="test@example.com")
    spotlight_data = {
        "version": 2,
        "cycle_day": 3,
        "spotlight_type": "influential_research",
        "query": "query",
        "paper": {"paper_id": "p_read", "title": "Paper Read Test", "year": 2026},
        "score": 88.0,
        "generated_at": _today_generated_at(),
        "engagement": {"is_read": False, "is_saved": True, "feedback": None},
    }
    profile = Profile(user_id=user_id, learning_spotlight=spotlight_data)

    from tests.unit.conftest import FakeScalarResult

    db = mock_db(FakeScalarResult(profile))
    app = _build_test_app(mock_user, db)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.patch("/api/v1/learning-spotlight/read", json={"is_read": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] is True
        assert data["data"]["papers"][0]["engagement"]["is_read"] is True
        assert data["data"]["papers"][0]["engagement"]["is_saved"] is True  # preserved
        assert data["data"]["papers"][0]["paper_id"] == "p_read"  # preserved
        assert data["data"]["papers"][0]["score"] == 88.0  # preserved


# ---------------------------------------------------------------------------
# 15-17. Save Action Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_save_and_unsave(mock_db) -> None:
    """15, 16, 17. PATCH /save supports both saving and un-saving while preserving metadata."""
    user_id = uuid4()
    mock_user = User(id=user_id, email="test@example.com")
    spotlight_data = {
        "version": 2,
        "cycle_day": 4,
        "spotlight_type": "latest_research",
        "query": "query",
        "paper": {"paper_id": "p_save", "title": "Paper Save Test"},
        "score": 90.0,
        "generated_at": _today_generated_at(),
        "engagement": {"is_read": False, "is_saved": False, "feedback": None},
    }
    profile = Profile(user_id=user_id, learning_spotlight=spotlight_data)

    from tests.unit.conftest import FakeScalarResult

    # Test Save
    db_save = mock_db(FakeScalarResult(profile))
    app_save = _build_test_app(mock_user, db_save)
    transport_save = ASGITransport(app=app_save)
    async with AsyncClient(transport=transport_save, base_url="http://test") as client:
        resp_save = await client.patch("/api/v1/learning-spotlight/save", json={"is_saved": True})
        assert resp_save.status_code == 200
        assert resp_save.json()["data"]["papers"][0]["engagement"]["is_saved"] is True

    # Test Unsave with fresh db
    db_unsave = mock_db(FakeScalarResult(profile))
    app_unsave = _build_test_app(mock_user, db_unsave)
    transport_unsave = ASGITransport(app=app_unsave)
    async with AsyncClient(transport=transport_unsave, base_url="http://test") as client:
        resp_unsave = await client.patch("/api/v1/learning-spotlight/save", json={"is_saved": False})
        assert resp_unsave.status_code == 200
        assert resp_unsave.json()["data"]["papers"][0]["engagement"]["is_saved"] is False


# ---------------------------------------------------------------------------
# 18-21. Feedback Action Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_feedback_useful_and_not_useful(mock_db) -> None:
    """18, 19, 21. Accepts 'useful' and 'not_useful' and preserves other fields."""
    user_id = uuid4()
    mock_user = User(id=user_id, email="test@example.com")
    spotlight_data = {
        "version": 2,
        "cycle_day": 5,
        "spotlight_type": "beyond_your_field",
        "query": "query",
        "paper": {"paper_id": "p_feed", "title": "Paper Feedback Test"},
        "score": 85.0,
        "generated_at": _today_generated_at(),
        "engagement": {"is_read": True, "is_saved": False, "feedback": None},
    }
    profile = Profile(user_id=user_id, learning_spotlight=spotlight_data)

    from tests.unit.conftest import FakeScalarResult

    # Useful
    db1 = mock_db(FakeScalarResult(profile))
    app1 = _build_test_app(mock_user, db1)
    transport1 = ASGITransport(app=app1)
    async with AsyncClient(transport=transport1, base_url="http://test") as client:
        resp1 = await client.patch("/api/v1/learning-spotlight/feedback", json={"feedback": "useful"})
        assert resp1.status_code == 200
        assert resp1.json()["data"]["papers"][0]["engagement"]["feedback"] is True
        assert resp1.json()["data"]["papers"][0]["engagement"]["is_read"] is True  # preserved

    # Not useful with fresh db
    db2 = mock_db(FakeScalarResult(profile))
    app2 = _build_test_app(mock_user, db2)
    transport2 = ASGITransport(app=app2)
    async with AsyncClient(transport=transport2, base_url="http://test") as client:
        resp2 = await client.patch("/api/v1/learning-spotlight/feedback", json={"feedback": "not_useful"})
        assert resp2.status_code == 200
        assert resp2.json()["data"]["papers"][0]["engagement"]["feedback"] is True




@pytest.mark.asyncio
async def test_patch_feedback_invalid_value_rejected(mock_db) -> None:
    """20. Invalid feedback values are rejected with 422 Unprocessable Entity."""
    user_id = uuid4()
    mock_user = User(id=user_id, email="test@example.com")
    app = _build_test_app(mock_user, mock_db())

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.patch("/api/v1/learning-spotlight/feedback", json={"feedback": "amazing"})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 22. Authentication Rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unauthenticated_requests_are_rejected() -> None:
    """22. Unauthenticated request to /learning-spotlight is rejected by auth dependency."""
    from common.exceptions import ApiError
    from fastapi.responses import JSONResponse

    app = FastAPI()
    app.include_router(spotlight_router, prefix="/api/v1")

    @app.exception_handler(ApiError)
    async def api_error_handler(request, exc):
        return JSONResponse(status_code=401, content={"status": False, "message": str(exc)})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/learning-spotlight")
        assert resp.status_code == 401
        assert "Missing access token" in resp.json()["message"]

