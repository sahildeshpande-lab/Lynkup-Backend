from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from apps.connections.repositories.connection_repository import (
    build_candidate_mutuals_from_connections,
)
from apps.connections.schemas import MutualRecommendedUserResponse
from apps.connections.services import mutual_connection_service as svc
from common.pagination import paginate_or_all
from core.database import get_session
from core.security.auth import get_current_user
from entrypoints.api import app


def _profile(
    user_id=None,
    *,
    first_name="Test",
    last_name="User",
    major=None,
    minor=None,
    university_id=None,
    edu_level=None,
    profile_photo_url=None,
):
    return SimpleNamespace(
        user_id=user_id or uuid.uuid4(),
        first_name=first_name,
        last_name=last_name,
        major=major,
        minor=minor,
        university_id=university_id,
        profile_interests_id=None,
        edu_level=edu_level,
        profile_photo_url=profile_photo_url,
    )


def _connection(user_a, user_b):
    low_id, high_id = (user_a, user_b) if user_a < user_b else (user_b, user_a)
    return SimpleNamespace(user_low_id=low_id, user_high_id=high_id)


def test_build_candidate_mutuals_from_connections_excludes_viewer():
    viewer_id = uuid.uuid4()
    friend_id = uuid.uuid4()
    candidate_id = uuid.uuid4()
    friend_ids = {friend_id}
    connections = [
        _connection(viewer_id, friend_id),
        _connection(friend_id, candidate_id),
    ]

    result = build_candidate_mutuals_from_connections(viewer_id, friend_ids, connections)

    assert viewer_id not in result
    assert result[candidate_id] == {friend_id}


def test_build_candidate_mutuals_counts_multiple_friends():
    viewer_id = uuid.uuid4()
    friend_a = uuid.uuid4()
    friend_b = uuid.uuid4()
    candidate_id = uuid.uuid4()
    friend_ids = {friend_a, friend_b}
    connections = [
        _connection(friend_a, candidate_id),
        _connection(friend_b, candidate_id),
    ]

    result = build_candidate_mutuals_from_connections(viewer_id, friend_ids, connections)

    assert result[candidate_id] == {friend_a, friend_b}


@pytest.mark.asyncio
async def test_get_mutual_recommendations_no_connections():
    db = AsyncMock()
    viewer_id = uuid.uuid4()

    with patch.object(
        svc.connection_repository,
        "fetch_second_hop_mutuals",
        AsyncMock(return_value=(set(), {})),
    ):
        results = await svc.get_mutual_recommendations(db, viewer_id)

    assert results == []


@pytest.mark.asyncio
async def test_get_mutual_recommendations_one_mutual():
    db = AsyncMock()
    viewer_id = uuid.uuid4()
    friend_id = uuid.uuid4()
    candidate_id = uuid.uuid4()
    candidate = _profile(candidate_id, first_name="Lio", last_name="Messi", major="BCA")
    friend = _profile(friend_id, first_name="John", last_name="Doe", profile_photo_url="john.png")

    with (
        patch.object(
            svc.connection_repository,
            "fetch_second_hop_mutuals",
            AsyncMock(return_value=({friend_id}, {candidate_id: {friend_id}})),
        ),
        patch.object(svc, "_get_excluded_user_ids", AsyncMock(return_value={friend_id})),
        patch.object(
            svc.connection_repository,
            "fetch_eligible_recommendation_profiles",
            AsyncMock(return_value=[(candidate, None, None, None)]),
        ),
        patch.object(
            svc.connection_repository,
            "fetch_visible_profiles_by_user_ids",
            AsyncMock(return_value={friend_id: friend}),
        ),
        patch(
            "apps.connections.services.get_relationship_flags",
            AsyncMock(return_value={}),
        ),
        patch.object(
            svc,
            "generate_profile_image_url",
            lambda name: f"https://cdn/{name}",
        ),
    ):
        results = await svc.get_mutual_recommendations(db, viewer_id)

    assert len(results) == 1
    item = results[0]
    assert item["user_id"] == candidate_id
    assert item["first_name"] == "Lio"
    assert item["last_name"] == "Messi"
    assert "score" not in item
    assert "match_reason" not in item
    assert "mutual_connections_count" not in item
    assert item["mutual_connections"]["count"] == 1
    assert len(item["mutual_connections"]["users"]) == 1
    mutual = item["mutual_connections"]["users"][0]
    assert mutual["user_id"] == friend_id
    assert mutual["first_name"] == "John"
    assert mutual["last_name"] == "Doe"
    assert mutual["profilePhoto_url"] == "https://cdn/john.png"


@pytest.mark.asyncio
async def test_get_mutual_recommendations_multiple_and_more_than_three():
    db = AsyncMock()
    viewer_id = uuid.uuid4()
    friends = [
        _profile(uuid.uuid4(), first_name="Zed", last_name="Zulu"),
        _profile(uuid.uuid4(), first_name="Amy", last_name="Adams"),
        _profile(uuid.uuid4(), first_name="Ben", last_name="Baker"),
        _profile(uuid.uuid4(), first_name="Cara", last_name="Cole"),
    ]
    friend_ids = {friend.user_id for friend in friends}
    candidate_one_id = uuid.uuid4()
    candidate_many_id = uuid.uuid4()
    candidate_one = _profile(candidate_one_id, first_name="One", last_name="Mutual")
    candidate_many = _profile(candidate_many_id, first_name="Many", last_name="Mutuals")

    candidate_mutuals = {
        candidate_one_id: {friends[0].user_id},
        candidate_many_id: friend_ids,
    }

    with (
        patch.object(
            svc.connection_repository,
            "fetch_second_hop_mutuals",
            AsyncMock(return_value=(friend_ids, candidate_mutuals)),
        ),
        patch.object(svc, "_get_excluded_user_ids", AsyncMock(return_value=set(friend_ids))),
        patch.object(
            svc.connection_repository,
            "fetch_eligible_recommendation_profiles",
            AsyncMock(
                return_value=[
                    (candidate_one, None, None, None),
                    (candidate_many, None, None, None),
                ]
            ),
        ),
        patch.object(
            svc.connection_repository,
            "fetch_visible_profiles_by_user_ids",
            AsyncMock(return_value={friend.user_id: friend for friend in friends}),
        ),
        patch(
            "apps.connections.services.get_relationship_flags",
            AsyncMock(return_value={}),
        ),
        patch.object(svc, "generate_profile_image_url", lambda name: name),
    ):
        results = await svc.get_mutual_recommendations(db, viewer_id)

    assert [item["user_id"] for item in results] == [candidate_many_id, candidate_one_id]

    many_item = results[0]
    assert many_item["mutual_connections"]["count"] == 4
    assert len(many_item["mutual_connections"]["users"]) == 3
    returned_names = [user["first_name"] for user in many_item["mutual_connections"]["users"]]
    assert returned_names == ["Amy", "Ben", "Cara"]
    for mutual in many_item["mutual_connections"]["users"]:
        assert set(mutual) == {"user_id", "first_name", "last_name", "profilePhoto_url"}

    one_item = results[1]
    assert one_item["mutual_connections"]["count"] == 1
    assert len(one_item["mutual_connections"]["users"]) == 1


@pytest.mark.asyncio
async def test_get_mutual_recommendations_excludes_connected_blocked_deleted_and_self():
    db = AsyncMock()
    viewer_id = uuid.uuid4()
    friend_id = uuid.uuid4()
    connected_id = uuid.uuid4()
    blocked_id = uuid.uuid4()
    deleted_id = uuid.uuid4()
    eligible_id = uuid.uuid4()
    eligible = _profile(eligible_id, first_name="Eligible", last_name="User")
    friend = _profile(friend_id, first_name="Friend", last_name="One")

    candidate_mutuals = {
        viewer_id: {friend_id},
        connected_id: {friend_id},
        blocked_id: {friend_id},
        deleted_id: {friend_id},
        eligible_id: {friend_id},
    }

    with (
        patch.object(
            svc.connection_repository,
            "fetch_second_hop_mutuals",
            AsyncMock(return_value=({friend_id, connected_id}, candidate_mutuals)),
        ),
        patch.object(
            svc,
            "_get_excluded_user_ids",
            AsyncMock(return_value={friend_id, connected_id, blocked_id}),
        ),
        patch.object(
            svc.connection_repository,
            "fetch_eligible_recommendation_profiles",
            AsyncMock(return_value=[(eligible, None, None, None)]),
        ) as fetch_eligible,
        patch.object(
            svc.connection_repository,
            "fetch_visible_profiles_by_user_ids",
            AsyncMock(return_value={friend_id: friend}),
        ),
        patch(
            "apps.connections.services.get_relationship_flags",
            AsyncMock(return_value={}),
        ),
        patch.object(svc, "generate_profile_image_url", lambda name: name),
    ):
        results = await svc.get_mutual_recommendations(db, viewer_id)

    requested_ids = set(fetch_eligible.await_args.args[2])
    assert viewer_id not in requested_ids
    assert connected_id not in requested_ids
    assert blocked_id not in requested_ids
    assert eligible_id in requested_ids
    assert [item["user_id"] for item in results] == [eligible_id]


@pytest.mark.asyncio
async def test_get_mutual_recommendations_orders_by_mutual_count_desc():
    db = AsyncMock()
    viewer_id = uuid.uuid4()
    friend_a = uuid.uuid4()
    friend_b = uuid.uuid4()
    friend_c = uuid.uuid4()
    low_id = uuid.uuid4()
    mid_id = uuid.uuid4()
    high_id = uuid.uuid4()
    low = _profile(low_id, first_name="Low")
    mid = _profile(mid_id, first_name="Mid")
    high = _profile(high_id, first_name="High")
    friends = {
        friend_a: _profile(friend_a, first_name="A"),
        friend_b: _profile(friend_b, first_name="B"),
        friend_c: _profile(friend_c, first_name="C"),
    }

    with (
        patch.object(
            svc.connection_repository,
            "fetch_second_hop_mutuals",
            AsyncMock(
                return_value=(
                    {friend_a, friend_b, friend_c},
                    {
                        low_id: {friend_a},
                        mid_id: {friend_a, friend_b},
                        high_id: {friend_a, friend_b, friend_c},
                    },
                )
            ),
        ),
        patch.object(svc, "_get_excluded_user_ids", AsyncMock(return_value=set())),
        patch.object(
            svc.connection_repository,
            "fetch_eligible_recommendation_profiles",
            AsyncMock(
                return_value=[
                    (low, None, None, None),
                    (mid, None, None, None),
                    (high, None, None, None),
                ]
            ),
        ),
        patch.object(
            svc.connection_repository,
            "fetch_visible_profiles_by_user_ids",
            AsyncMock(return_value=friends),
        ),
        patch(
            "apps.connections.services.get_relationship_flags",
            AsyncMock(return_value={}),
        ),
        patch.object(svc, "generate_profile_image_url", lambda name: name),
    ):
        results = await svc.get_mutual_recommendations(db, viewer_id)

    assert [item["mutual_connections"]["count"] for item in results] == [3, 2, 1]
    assert [item["user_id"] for item in results] == [high_id, mid_id, low_id]
    assert len(results[0]["mutual_connections"]["users"]) == 3
    assert len(results[1]["mutual_connections"]["users"]) == 2
    assert len(results[2]["mutual_connections"]["users"]) == 1


def test_paginate_or_all_default_returns_all_records():
    items = [{"id": index} for index in range(5)]
    result = paginate_or_all(items)
    assert result.items == items
    assert result.page == 1
    assert result.pageSize == 5
    assert result.totalItems == 5


def test_paginate_or_all_page_and_page_size():
    items = [{"id": index} for index in range(5)]
    result = paginate_or_all(items, page=1, page_size=2)
    assert result.items == items[:2]
    assert result.page == 1
    assert result.pageSize == 2
    assert result.totalItems == 5
    assert result.totalPages == 3


def test_mutual_recommended_user_response_omits_score_fields():
    payload = MutualRecommendedUserResponse(
        user_id=uuid.uuid4(),
        first_name="Lio",
        last_name="Messi",
        major="BCA",
        mutual_connections={
            "count": 2,
            "users": [
                {
                    "user_id": uuid.uuid4(),
                    "first_name": "John",
                    "last_name": "Doe",
                    "profilePhoto_url": None,
                }
            ],
        },
    )
    dumped = payload.model_dump()
    assert "score" not in dumped
    assert "match_reason" not in dumped
    assert "mutual_connections_count" not in dumped
    assert dumped["mutual_connections"]["count"] == 2
    assert dumped["mutual_connections"]["users"][0]["first_name"] == "John"


def _candidate_item(first_name: str, mutual_count: int) -> dict:
    return {
        "user_id": uuid.uuid4(),
        "first_name": first_name,
        "last_name": "Test",
        "university": None,
        "university_details": {
            "id": None,
            "university_name": None,
            "university_website": None,
        },
        "major": None,
        "minor": None,
        "edu_level": None,
        "profilePhoto_url": None,
        "is_deleted": False,
        "is_connected": False,
        "is_followed": False,
        "is_blocked": False,
        "request_sent": False,
        "request_received": False,
        "mutual_connections": {
            "count": mutual_count,
            "users": [
                {
                    "user_id": uuid.uuid4(),
                    "first_name": "Friend",
                    "last_name": "One",
                    "profilePhoto_url": None,
                }
            ],
        },
    }


@pytest.mark.asyncio
async def test_mutual_recommendations_route_default_returns_all_and_paginates():
    viewer = SimpleNamespace(id=uuid.uuid4())
    items = [
        _candidate_item("Grace", 4),
        _candidate_item("Frank", 2),
        _candidate_item("Eve", 1),
    ]

    async def _override_user():
        return viewer

    async def _override_db():
        return AsyncMock()

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_session] = _override_db
    try:
        with patch(
            "apps.connections.routes.get_mutual_recommendations",
            AsyncMock(return_value=items),
        ):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                all_response = await client.get("/api/v1/connections/mutual-recommendations")
                assert all_response.status_code == 200
                all_data = all_response.json()["data"]
                assert [item["first_name"] for item in all_data["items"]] == ["Grace", "Frank", "Eve"]
                assert all_data["page"] == 1
                assert all_data["pageSize"] == 3
                assert all_data["totalItems"] == 3
                assert all_data["totalPages"] == 1
                assert "score" not in all_data["items"][0]
                assert "match_reason" not in all_data["items"][0]
                assert "mutual_connections_count" not in all_data["items"][0]
                assert all_data["items"][0]["mutual_connections"]["count"] == 4

                page_response = await client.get(
                    "/api/v1/connections/mutual-recommendations",
                    params={"page": 1, "pageSize": 2},
                )
                assert page_response.status_code == 200
                page_data = page_response.json()["data"]
                assert page_data["page"] == 1
                assert page_data["pageSize"] == 2
                assert page_data["totalItems"] == 3
                assert page_data["totalPages"] == 2
                assert len(page_data["items"]) == 2
                assert page_data["items"][0]["first_name"] == "Grace"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
