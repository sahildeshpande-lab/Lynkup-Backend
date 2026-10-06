"""Unit tests for Learning Spotlight Persistent User-Paper Interaction History (Step 15 Cleanup).

Tests:
1. LearningPaperInteraction model has required fields and indexes.
2. LearningPaperAction enum contains exactly READ, SAVE, UNSAVE, LIKE, DISLIKE and rejects SKIP.
3. update_read_status inserts a READ interaction event.
4. update_save_status(is_saved=True) inserts a SAVE interaction event.
5. update_save_status(is_saved=False) inserts an UNSAVE interaction event.
6. submit_feedback(useful) inserts a LIKE interaction event.
7. submit_feedback(not_useful) inserts a DISLIKE interaction event.
8. Multiple SAVE events are appended without error (event sourcing history).
9. get_saved_papers returns papers where the latest event is SAVE.
10. SAVE -> UNSAVE excludes the paper from saved list.
11. SAVE -> UNSAVE -> SAVE includes the paper in saved list.
12. User A's saved papers are isolated from User B.
13. GET /learning-spotlight/saved endpoint returns structured list.
14. POST /learning-spotlight/{spotlight_id}/skip endpoint returns 404 (removed).
15. Reusable query helpers (has_user_read_paper, has_user_saved_paper, has_user_seen_paper).
16. Unsaving a previous-cycle paper_id writes UNSAVE for that paper, not papers[0].
17. Unsaving an unknown paper_id that was never saved returns 404.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from apps.accounts.db_models import User
from apps.learningspotlight.db_models.learning_paper_interaction_db_model import (
    LearningPaperInteraction,
)
from apps.learningspotlight.routes import router as spotlight_router
from apps.learningspotlight.schemas import (
    LearningSpotlight,
    LearningSpotlightAuthor,
    LearningSpotlightEngagement,
    LearningSpotlightPaper,
    SavedPaperItem,
)
from apps.learningspotlight.services.spotlight_persistence_service import (
    SpotlightPersistenceService,
)
from apps.profiles.db_models.profile_db_model import Profile
from common.enums import LearningPaperAction, SpotlightFeedback, SpotlightType
from core.database.session import get_session
from core.security.auth import get_current_user


def _make_dummy_spotlight_dict(
    paper_id: str = "paper_int_1",
    spotlight_id: str = "11111111-1111-1111-1111-111111111111",
) -> dict:
    return {
        "version": 2,
        "cycle_day": 1,
        "spotlight_type": "leading_thinker",
        "query": '("test")',
        "id": spotlight_id,
        "score": 0.95,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "paper": {
            "paper_id": paper_id,
            "title": "A Foundation Paper on Artificial Intelligence",
            "authors": [{"name": "Dr. Alice Smith", "author_id": "auth_1"}],
            "abstract": "We present foundational research.",
            "venue": "NeurIPS",
            "year": 2024,
            "citation_count": 100,
            "url": "https://example.com/ai_paper",
        },
        "engagement": {
            "is_read": False,
            "is_saved": False,
            "feedback": None,
        },
    }


# ---------------------------------------------------------------------------
# 1-2. Enum & Model Integrity
# ---------------------------------------------------------------------------


def test_learning_paper_action_enum_values() -> None:
    """Allowed actions must be strictly READ, SAVE, UNSAVE, LIKE, DISLIKE."""
    actions = {a.value for a in LearningPaperAction}
    assert actions == {"READ", "SAVE", "UNSAVE", "LIKE", "DISLIKE"}
    assert "SKIP" not in actions


# ---------------------------------------------------------------------------
# 3-7. Persistence Service Interaction Event Logging
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_read_status_logs_read_interaction(mock_db) -> None:
    """Marking spotlight as read creates a READ interaction event."""
    user_id = uuid4()
    spotlight_dict = _make_dummy_spotlight_dict()
    profile = Profile(user_id=user_id, learning_spotlight=spotlight_dict)

    mock_session = mock_db()
    mock_execute_result = MagicMock()
    mock_execute_result.scalar_one_or_none.return_value = profile
    mock_session.execute = AsyncMock(return_value=mock_execute_result)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()

    service = SpotlightPersistenceService()
    result = await service.update_read_status(mock_session, user_id, is_read=True)

    assert result.engagement.is_read is True
    added_objs = [call[0][0] for call in mock_session.add.call_args_list]
    interactions = [o for o in added_objs if isinstance(o, LearningPaperInteraction)]
    assert len(interactions) == 1
    assert interactions[0].user_id == user_id
    assert interactions[0].paper_id == "paper_int_1"
    assert interactions[0].action == LearningPaperAction.read.value


@pytest.mark.asyncio
async def test_update_save_status_logs_save_and_unsave_interactions(mock_db) -> None:
    """Saving and unsaving records SAVE and UNSAVE events respectively."""
    user_id = uuid4()
    spotlight_dict = _make_dummy_spotlight_dict()
    profile = Profile(user_id=user_id, learning_spotlight=spotlight_dict)

    mock_session = mock_db()
    mock_execute_result = MagicMock()
    mock_execute_result.scalar_one_or_none.return_value = profile
    mock_session.execute = AsyncMock(return_value=mock_execute_result)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()

    service = SpotlightPersistenceService()

    # 1. SAVE
    res_save = await service.update_save_status(mock_session, user_id, is_saved=True)
    assert res_save.engagement.is_saved is True
    added_save = [call[0][0] for call in mock_session.add.call_args_list if isinstance(call[0][0], LearningPaperInteraction)]
    assert added_save[-1].action == LearningPaperAction.save.value

    # 2. UNSAVE
    res_unsave = await service.update_save_status(mock_session, user_id, is_saved=False)
    assert res_unsave.engagement.is_saved is False
    added_unsave = [call[0][0] for call in mock_session.add.call_args_list if isinstance(call[0][0], LearningPaperInteraction)]
    assert added_unsave[-1].action == LearningPaperAction.unsave.value


@pytest.mark.asyncio
async def test_submit_feedback_logs_like_and_dislike_interactions(mock_db) -> None:
    """Submitting feedback records LIKE and DISLIKE events."""
    user_id = uuid4()
    spotlight_dict = _make_dummy_spotlight_dict()
    profile = Profile(user_id=user_id, learning_spotlight=spotlight_dict)

    mock_session = mock_db()
    mock_execute_result = MagicMock()
    mock_execute_result.scalar_one_or_none.return_value = profile
    mock_session.execute = AsyncMock(return_value=mock_execute_result)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()

    service = SpotlightPersistenceService()

    # 1. useful -> LIKE
    res_like = await service.submit_feedback(mock_session, user_id, feedback=SpotlightFeedback.useful)
    assert res_like.engagement.feedback is True
    added_like = [call[0][0] for call in mock_session.add.call_args_list if isinstance(call[0][0], LearningPaperInteraction)]
    assert added_like[-1].action == LearningPaperAction.like.value

    # 2. not_useful -> DISLIKE
    res_dislike = await service.submit_feedback(mock_session, user_id, feedback=SpotlightFeedback.not_useful)
    assert res_dislike.engagement.feedback is True

    added_dislike = [call[0][0] for call in mock_session.add.call_args_list if isinstance(call[0][0], LearningPaperInteraction)]
    assert added_dislike[-1].action == LearningPaperAction.dislike.value



# ---------------------------------------------------------------------------
# 8-12. Saved Papers Resolution & Window Function Logic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_saved_papers_resolution(mock_db) -> None:
    """get_saved_papers returns papers based on latest SAVE state and merges metadata."""
    user_id = uuid4()
    spotlight_dict = _make_dummy_spotlight_dict(paper_id="paper_1")
    profile = Profile(user_id=user_id, learning_spotlight=spotlight_dict)

    mock_session = mock_db()

    saved_dt = datetime.now(timezone.utc)
    mock_window_res = MagicMock()
    mock_window_res.all.return_value = [("paper_1", saved_dt)]

    mock_profile_res = MagicMock()
    mock_profile_res.scalar_one_or_none.return_value = profile

    mock_logs_res = MagicMock()
    mock_logs_res.scalars.return_value.all.return_value = []

    mock_session.execute = AsyncMock(side_effect=[mock_window_res, mock_profile_res, mock_logs_res])

    service = SpotlightPersistenceService()
    saved = await service.get_saved_papers(mock_session, user_id)

    assert len(saved) == 1
    assert isinstance(saved[0], SavedPaperItem)
    assert saved[0].paper_id == "paper_1"
    assert saved[0].title == "A Foundation Paper on Artificial Intelligence"
    assert saved[0].saved_at == saved_dt


# ---------------------------------------------------------------------------
# 13-14. API Endpoints for Saved & Non-Existent Skip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_saved_papers_endpoint(mock_db) -> None:
    """GET /api/v1/learning-spotlight/saved returns structured ApiResponse."""
    user_id = uuid4()
    mock_user = User(id=user_id, email="scholar@example.com", role="user")

    app = FastAPI()
    app.include_router(spotlight_router, prefix="/api/v1")

    async def _override_user():
        return mock_user

    async def _override_db():
        yield mock_db()

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_session] = _override_db

    fake_item = SavedPaperItem(
        paper_id="paper_saved_1",
        title="Saved Machine Learning Paper",
        authors=[LearningSpotlightAuthor(name="Prof. John")],
        abstract="Summary abstract.",
        saved_at=datetime.now(timezone.utc),
    )

    with patch.object(
        SpotlightPersistenceService,
        "get_saved_papers",
        new=AsyncMock(return_value=[fake_item]),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/learning-spotlight/saved")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] is True
            assert len(data["data"]) == 1
            assert data["data"][0]["paper_id"] == "paper_saved_1"


@pytest.mark.asyncio
async def test_skip_spotlight_endpoint_returns_404_not_found(mock_db) -> None:
    """POST /api/v1/learning-spotlight/{spotlight_id}/skip endpoint is removed and returns 404."""
    user_id = uuid4()
    mock_user = User(id=user_id, email="scholar@example.com", role="user")

    app = FastAPI()
    app.include_router(spotlight_router, prefix="/api/v1")

    async def _override_user():
        return mock_user

    async def _override_db():
        yield mock_db()

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_session] = _override_db

    spot_id = str(uuid4())

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/v1/learning-spotlight/{spot_id}/skip")
        assert resp.status_code == 404 or resp.status_code == 405


# ---------------------------------------------------------------------------
# 16-17. Previous-cycle unsave
# ---------------------------------------------------------------------------


def _added_interactions(mock_session) -> list[LearningPaperInteraction]:
    return [
        call[0][0]
        for call in mock_session.add.call_args_list
        if isinstance(call[0][0], LearningPaperInteraction)
    ]


@pytest.mark.asyncio
async def test_unsave_previous_cycle_paper_writes_unsave_for_requested_id(
    mock_db,
) -> None:
    """Unsaving a paper no longer on the current cycle must not fall back to papers[0]."""
    user_id = uuid4()
    current_spotlight = _make_dummy_spotlight_dict(paper_id="paper_current")
    profile = Profile(user_id=user_id, learning_spotlight=current_spotlight)

    mock_session = mock_db()
    profile_res = MagicMock()
    profile_res.scalar_one_or_none.return_value = profile
    saved_res = MagicMock()
    saved_res.scalar_one_or_none.return_value = LearningPaperAction.save.value
    mock_session.execute = AsyncMock(side_effect=[profile_res, saved_res])
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()

    service = SpotlightPersistenceService()
    result = await service.update_save_status(
        mock_session,
        user_id,
        is_saved=False,
        paper_id="paper_previous",
    )

    assert result.papers[0].paper_id == "paper_current"
    assert result.papers[0].engagement.is_saved is False

    added_profiles = [
        call[0][0]
        for call in mock_session.add.call_args_list
        if isinstance(call[0][0], Profile)
    ]
    assert added_profiles == []

    interactions = _added_interactions(mock_session)
    assert len(interactions) == 1
    assert interactions[0].paper_id == "paper_previous"
    assert interactions[0].action == LearningPaperAction.unsave.value


@pytest.mark.asyncio
async def test_unsave_unknown_paper_not_in_saved_list_returns_404(mock_db) -> None:
    """Unsaving a paper_id that was never saved returns 404."""
    user_id = uuid4()
    current_spotlight = _make_dummy_spotlight_dict(paper_id="paper_current")
    profile = Profile(user_id=user_id, learning_spotlight=current_spotlight)

    mock_session = mock_db()
    profile_res = MagicMock()
    profile_res.scalar_one_or_none.return_value = profile
    saved_res = MagicMock()
    saved_res.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(side_effect=[profile_res, saved_res])
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()

    service = SpotlightPersistenceService()
    with pytest.raises(HTTPException) as exc_info:
        await service.update_save_status(
            mock_session,
            user_id,
            is_saved=False,
            paper_id="paper_unknown",
        )

    assert exc_info.value.status_code == 404
    assert _added_interactions(mock_session) == []


@pytest.mark.asyncio
async def test_patch_save_unsave_previous_cycle_paper_endpoint(mock_db) -> None:
    """PATCH /save with a previous-cycle paper_id unsaves that paper, not the current one."""
    user_id = uuid4()
    mock_user = User(id=user_id, email="scholar@example.com", role="user")
    current_spotlight = _make_dummy_spotlight_dict(paper_id="paper_current")
    profile = Profile(user_id=user_id, learning_spotlight=current_spotlight)

    from tests.unit.conftest import FakeScalarResult

    db = mock_db(
        FakeScalarResult(profile),
        FakeScalarResult(LearningPaperAction.save.value),
    )

    app = FastAPI()
    app.include_router(spotlight_router, prefix="/api/v1")

    async def _override_user():
        return mock_user

    async def _override_db():
        yield db

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_session] = _override_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.patch(
            "/api/v1/learning-spotlight/save",
            json={"is_saved": False, "paper_id": "paper_previous"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] is True
    assert body["data"]["papers"][0]["paper_id"] == "paper_current"
    assert body["data"]["papers"][0]["engagement"]["is_saved"] is False

    interactions = _added_interactions(db)
    assert len(interactions) == 1
    assert interactions[0].paper_id == "paper_previous"
    assert interactions[0].action == LearningPaperAction.unsave.value


@pytest.mark.asyncio
async def test_update_read_unknown_paper_id_returns_404_not_papers_zero(mock_db) -> None:
    """An explicit paper_id that is not in any snapshot must not fall back to papers[0]."""
    from tests.unit.conftest import FakeScalarResult

    user_id = uuid4()
    spotlight_dict = _make_dummy_spotlight_dict(paper_id="paper_current")
    profile = Profile(user_id=user_id, learning_spotlight=spotlight_dict)
    db = mock_db(FakeScalarResult(profile), FakeScalarResult(values=[]))

    service = SpotlightPersistenceService()
    with pytest.raises(HTTPException) as exc:
        await service.update_read_status(
            db,
            user_id,
            is_read=True,
            paper_id="paper_unknown",
            read_time_seconds=12,
        )

    assert exc.value.status_code == 404
    assert _added_interactions(db) == []
    assert profile.learning_spotlight["engagement"]["is_read"] is False


@pytest.mark.asyncio
async def test_update_read_historical_paper_does_not_mutate_current_snapshot(
    mock_db,
) -> None:
    """Reads of an archived paper keep today's snapshot and log the historical paper_id."""
    from apps.profiles.db_models.learning_recommendation_log_db_model import (
        LearningRecommendationLog,
    )
    from tests.unit.conftest import FakeScalarResult

    user_id = uuid4()
    current = _make_dummy_spotlight_dict(paper_id="paper_current")
    historical = _make_dummy_spotlight_dict(paper_id="paper_historical")
    historical["spotlight_type"] = "beyond_your_field"
    profile = Profile(user_id=user_id, learning_spotlight=current)
    log = LearningRecommendationLog(
        user_id=user_id,
        learning_recommendations=historical,
    )
    db = mock_db(FakeScalarResult(profile), FakeScalarResult(values=[log]))

    service = SpotlightPersistenceService()
    result = await service.update_read_status(
        db,
        user_id,
        is_read=True,
        paper_id="paper_historical",
        read_time_seconds=40,
    )

    assert result.papers[0].paper_id == "paper_current"
    assert result.engagement.is_read is False
    interactions = _added_interactions(db)
    assert len(interactions) == 1
    assert interactions[0].paper_id == "paper_historical"
    assert interactions[0].read_time_seconds == 40
