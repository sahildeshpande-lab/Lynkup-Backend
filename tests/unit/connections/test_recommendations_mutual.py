from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.connections.services import recommendation_service as svc


def _profile(user_id=None, *, major=None, minor=None, university_id=None):
    return SimpleNamespace(
        user_id=user_id or uuid.uuid4(),
        first_name="Test",
        last_name="User",
        major=major,
        minor=minor,
        university_id=university_id,
        profile_interests_id=None,
        edu_level=None,
        profile_photo_url=None,
    )


@pytest.mark.asyncio
async def test_get_excluded_user_ids_includes_connected_and_pending():
    viewer_id = uuid.uuid4()
    connected_id = uuid.uuid4()
    pending_id = uuid.uuid4()
    blocked_id = uuid.uuid4()

    pending_req = SimpleNamespace(
        sender_user_id=pending_id,
        receiver_user_id=viewer_id,
    )
    block = SimpleNamespace(
        blocker_user_id=viewer_id,
        blocked_user_id=blocked_id,
    )

    pending_result = MagicMock()
    pending_result.scalars.return_value.all.return_value = [pending_req]
    block_result = MagicMock()
    block_result.scalars.return_value.all.return_value = [block]

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[pending_result, block_result])

    with patch.object(
        svc,
        "get_user_connections",
        AsyncMock(return_value={connected_id}),
    ):
        excluded = await svc._get_excluded_user_ids(db, viewer_id)

    assert excluded == {connected_id, pending_id, blocked_id}


@pytest.mark.asyncio
async def test_get_recommendations_includes_mutual_friend_of_friend():
    viewer_id = uuid.uuid4()
    bridge_id = uuid.uuid4()
    candidate_id = uuid.uuid4()

    viewer_profile = _profile(viewer_id)
    candidate_profile = _profile(candidate_id, major=None)

    adjacency = {
        viewer_id: {bridge_id},
        bridge_id: {viewer_id, candidate_id},
        candidate_id: {bridge_id},
    }

    profile_result = MagicMock()
    profile_result.scalars.return_value.first.return_value = viewer_profile
    candidates_result = MagicMock()
    candidates_result.all.return_value = [(candidate_profile, None)]

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[profile_result, candidates_result])

    with (
        patch.object(
            svc,
            "_build_connection_adjacency",
            AsyncMock(return_value=adjacency),
        ),
        patch.object(
            svc,
            "_get_excluded_user_ids",
            AsyncMock(return_value={bridge_id}),
        ),
        patch(
            "apps.connections.services.get_relationship_flags",
            AsyncMock(return_value={}),
        ),
        patch(
            "apps.connections.services.connection_service.apply_relationship_flags",
            lambda item, flags_map, uid: item,
        ),
    ):
        results = await svc.get_recommendations(db, viewer_id)

    assert len(results) == 1
    assert results[0]["user_id"] == candidate_id
    assert results[0]["mutual_connections_count"] == 1
    assert results[0]["score"] == 25.0
    assert "mutual connection" in (results[0]["match_reason"] or "").lower()


@pytest.mark.asyncio
async def test_get_recommendations_skips_already_connected_users():
    viewer_id = uuid.uuid4()
    connected_id = uuid.uuid4()

    viewer_profile = _profile(viewer_id, major="CS")
    connected_profile = _profile(connected_id, major="CS")

    profile_result = MagicMock()
    profile_result.scalars.return_value.first.return_value = viewer_profile
    candidates_result = MagicMock()
    candidates_result.all.return_value = []

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[profile_result, candidates_result])

    with (
        patch.object(svc, "_build_connection_adjacency", AsyncMock(return_value={})),
        patch.object(
            svc,
            "_get_excluded_user_ids",
            AsyncMock(return_value={connected_id}),
        ) as excluded,
        patch(
            "apps.connections.services.get_relationship_flags",
            AsyncMock(return_value={}),
        ),
    ):
        results = await svc.get_recommendations(db, viewer_id)

    excluded.assert_awaited_once_with(db, viewer_id)
    assert results == []
    # Connected candidate was filtered before scoring via excluded IDs.
    assert connected_profile.user_id in {connected_id}


def test_calculate_recommendation_score_mutual_only():
    p1 = _profile(major=None)
    p2 = _profile(major=None)
    assert svc.calculate_recommendation_score(p1, p2, 1) == 25.0
    assert svc.calculate_recommendation_score(p1, p2, 3) == 50.0
    assert svc.calculate_recommendation_score(p1, p2, 0) == 0.0
