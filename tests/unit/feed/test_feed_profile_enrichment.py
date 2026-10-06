"""Phase 4: combined feed profile enrichment."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.feed.services.profile_enrichment import (
    FeedProfileEnrichment,
    _FEED_PROFILE_ENRICHMENT_SQL,
    load_feed_profile_enrichment,
    map_feed_profile_enrichment_payload,
)


def test_map_empty_lookups_preserves_profile_scalar_fields():
    user_id = uuid.uuid4()
    profile = SimpleNamespace(
        university_id=None,
        profile_interests_id=[],
        bio="bio",
        major="CS",
        minor=None,
        edu_level="Bachelors",
    )
    mapped = map_feed_profile_enrichment_payload(
        {user_id: profile},
        universities=[],
        interests=[],
        requested_user_ids=[],
    )
    assert mapped.requested_user_ids == set()
    assert mapped.connected_user_ids == set()
    assert mapped.profile_details[user_id]["bio"] == "bio"
    assert mapped.profile_details[user_id]["university"] is None
    assert mapped.profile_details[user_id]["academic_interest"] == []


def test_map_universities_interests_and_requested_equivalence():
    user_id = uuid.uuid4()
    uni_id = uuid.uuid4()
    other = uuid.uuid4()
    profile = SimpleNamespace(
        university_id=uni_id,
        profile_interests_id=["2", 1, "bad"],
        bio="Profile bio",
        major="Computer Science",
        minor="Mathematics",
        edu_level="Masters",
    )
    mapped = map_feed_profile_enrichment_payload(
        {user_id: profile},
        universities=[
            {"id": str(uni_id), "name": "Lynkup University", "website": "https://u.example"}
        ],
        interests=[
            {"id": 1, "name": "AI"},
            {"id": 2, "name": "Data Science"},
        ],
        requested_user_ids=[str(other), str(user_id)],
    )
    assert mapped.profile_details[user_id] == {
        "university": "Lynkup University",
        "university_details": {
            "id": str(uni_id),
            "university_name": "Lynkup University",
            "university_website": "https://u.example",
        },
        "bio": "Profile bio",
        "academic_interest": ["Data Science", "AI"],
        "major": "Computer Science",
        "minor": "Mathematics",
        "education_level": "Masters",
    }
    assert mapped.requested_user_ids == {other, user_id}


def test_sql_avoids_cartesian_join():
    assert "json_agg" in _FEED_PROFILE_ENRICHMENT_SQL
    assert "university_rows" in _FEED_PROFILE_ENRICHMENT_SQL
    assert "interest_rows" in _FEED_PROFILE_ENRICHMENT_SQL
    assert "requested_rows" in _FEED_PROFILE_ENRICHMENT_SQL
    assert "connection_rows" in _FEED_PROFILE_ENRICHMENT_SQL
    # No join across enrichment domains.
    assert "JOIN interest_rows" not in _FEED_PROFILE_ENRICHMENT_SQL
    assert "JOIN university_rows" not in _FEED_PROFILE_ENRICHMENT_SQL


@pytest.mark.asyncio
async def test_load_feed_profile_enrichment_empty_profiles_skips_sql():
    db = AsyncMock()
    result = await load_feed_profile_enrichment(db, uuid.uuid4(), {})
    assert result == FeedProfileEnrichment({}, set(), set())
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_load_feed_profile_enrichment_single_sql_round_trip():
    user_id = uuid.uuid4()
    uni_id = uuid.uuid4()
    viewer = uuid.uuid4()
    profile = SimpleNamespace(
        university_id=uni_id,
        profile_interests_id=[1],
        bio="b",
        major="m",
        minor=None,
        edu_level=None,
    )
    mapping = MagicMock()
    mapping.mappings.return_value.one.return_value = {
        "universities": [{"id": str(uni_id), "name": "U", "website": None}],
        "interests": [{"id": 1, "name": "AI"}],
        "requested_user_ids": [str(user_id)],
        "connected_user_ids": [str(user_id)],
    }
    db = AsyncMock()
    db.execute = AsyncMock(return_value=mapping)

    result = await load_feed_profile_enrichment(
        db, viewer, {user_id: profile}
    )
    assert db.execute.await_count == 1
    params = db.execute.await_args.args[1]
    assert params["viewer_user_id"] == viewer
    assert uni_id in params["university_ids"]
    assert 1 in params["interest_ids"]
    assert user_id in params["target_user_ids"]
    assert result.profile_details[user_id]["university"] == "U"
    assert result.profile_details[user_id]["academic_interest"] == ["AI"]
    assert result.requested_user_ids == {user_id}
    assert result.connected_user_ids == {user_id}
