from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.connections.services import recommendation_service as svc


def _profile(
    user_id=None,
    *,
    major=None,
    minor=None,
    university_id=None,
    edu_level=None,
    profile_interests_id=None,
    first_name="Test",
    last_name="User",
):
    return SimpleNamespace(
        user_id=user_id or uuid.uuid4(),
        first_name=first_name,
        last_name=last_name,
        major=major,
        minor=minor,
        university_id=university_id,
        profile_interests_id=profile_interests_id,
        edu_level=edu_level,
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
    # Service unpacks (Profile, University.id, University.name, University.website)
    candidates_result.all.return_value = [(candidate_profile, None, None, None)]

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
    assert results[0]["score"] == 10.0
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
    assert svc.calculate_recommendation_score(p1, p2, 0) == 0.0
    assert svc.calculate_recommendation_score(p1, p2, 1) == 10.0
    assert svc.calculate_recommendation_score(p1, p2, 2) == 20.0
    assert svc.calculate_recommendation_score(p1, p2, 3) == 20.0
    assert svc.calculate_recommendation_score(p1, p2, 4) == 20.0


def test_calculate_recommendation_score_other_weights_unchanged():
    university_id = uuid.uuid4()
    p1 = _profile(
        major="CS",
        minor="Math",
        university_id=university_id,
        edu_level="Bachelors",
        profile_interests_id=[1, 2],
    )
    p2 = _profile(
        major="CS",
        minor="Math",
        university_id=university_id,
        edu_level="Bachelors",
        profile_interests_id=[1, 3],
    )
    # major +30, minor +15, university +20, interests +20, education +10
    assert svc.calculate_recommendation_score(p1, p2, 0) == 95.0
    # plus 1 mutual (+10) exceeds 100 and stays capped
    assert svc.calculate_recommendation_score(p1, p2, 1) == 100.0

    assert svc.calculate_recommendation_score(_profile(major="CS"), _profile(major="CS"), 0) == 30.0
    assert svc.calculate_recommendation_score(_profile(minor="Math"), _profile(minor="Math"), 0) == 15.0
    assert (
        svc.calculate_recommendation_score(
            _profile(university_id=university_id),
            _profile(university_id=university_id),
            0,
        )
        == 20.0
    )
    assert (
        svc.calculate_recommendation_score(
            _profile(profile_interests_id=[1, 2]),
            _profile(profile_interests_id=[2, 3]),
            0,
        )
        == 20.0
    )
    assert (
        svc.calculate_recommendation_score(
            _profile(edu_level="Bachelors"),
            _profile(edu_level="Bachelors"),
            0,
        )
        == 10.0
    )


@pytest.mark.asyncio
async def test_get_recommendations_categorized_splits_major_minor_and_without():
    viewer_id = uuid.uuid4()
    uni_id = uuid.uuid4()
    viewer_profile = _profile(viewer_id, major="Accounting", minor="Finance", university_id=uni_id)

    # Create 10 candidates with matching major/minor
    # Service unpacks (Profile, University.id, University.name, University.website)
    mm_candidates = [
        (_profile(uuid.uuid4(), major="Accounting"), uni_id, "University A", None)
        for _ in range(10)
    ]
    # Create 10 candidates without matching major/minor (e.g. same university)
    uni_candidates = [
        (
            _profile(uuid.uuid4(), major="Biology", minor="Chemistry", university_id=uni_id),
            uni_id,
            "University A",
            None,
        )
        for _ in range(10)
    ]

    profile_result = MagicMock()
    profile_result.scalars.return_value.first.return_value = viewer_profile
    candidates_result = MagicMock()
    candidates_result.all.return_value = mm_candidates + uni_candidates

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[profile_result, candidates_result])

    with (
        patch.object(svc, "_build_connection_adjacency", AsyncMock(return_value={})),
        patch.object(svc, "_get_excluded_user_ids", AsyncMock(return_value=set())),
        patch("apps.connections.services.get_relationship_flags", AsyncMock(return_value={})),
        patch("apps.connections.services.connection_service.apply_relationship_flags", lambda item, flags_map, uid: item),
    ):
        categorized = await svc.get_recommendations_categorized(db, viewer_id)

    # based_on_major_minor should have 8 users (4 major/minor + 4 university)
    assert len(categorized["based_on_major_minor"]) == 8
    mm_count = sum(1 for item in categorized["based_on_major_minor"] if item["major"] == "Accounting")
    uni_count = sum(1 for item in categorized["based_on_major_minor"] if item["major"] == "Biology")
    assert mm_count == 4
    assert uni_count == 4

    # without_major_minor should cap at 8 users
    assert len(categorized["without_major_minor"]) == 8
    for item in categorized["without_major_minor"]:
        assert item["major"] == "Biology"

    # items contains all scored candidates
    assert len(categorized["items"]) == 20

