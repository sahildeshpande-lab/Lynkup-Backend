from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest

from apps.health_check.services import check_database
from apps.moderation.schemas import UpdateModerationWordsRequest
from apps.moderation.services import moderation_words_service
from apps.profiles.db_models import CompletenessWeight, Profile
from apps.profiles.services import completeness_service
from apps.search.schemas import UniversitySearchParams
from apps.search.services import (
    create_academic_interest,
    get_academic_interests,
    get_academics_info,
    search_universities,
)


@pytest.mark.asyncio
async def test_health_check_database_uses_scalar_result(mock_db, scalar_result):
    db = mock_db(scalar_result(1))
    assert await check_database(db) is True
    db.execute.assert_awaited_once()


def test_moderation_word_helpers_normalize_and_serialize():
    assert moderation_words_service._normalize_words([" Bad ", "bad", "", "Word"]) == ["bad", "word"]
    assert moderation_words_service._to_response_data(None) == {"profanityWords": []}
    config = SimpleNamespace(profanity_words=["one", "two"])
    assert moderation_words_service._to_response_data(config) == {"profanityWords": ["one", "two"]}


@pytest.mark.asyncio
async def test_update_moderation_words_creates_and_updates(mock_db, scalar_result):
    create_db = mock_db(scalar_result(None))
    created = await moderation_words_service.update_moderation_words(
        UpdateModerationWordsRequest(profanityWords=[" One ", "one", "Two"]),
        create_db,
    )
    assert created == {"profanityWords": ["one", "two"]}
    assert create_db.add.called
    create_db.commit.assert_awaited_once()
    create_db.refresh.assert_awaited_once()

    existing_config = SimpleNamespace(profanity_words=["old"], updated_at=None)
    update_db = mock_db(scalar_result(existing_config))
    updated = await moderation_words_service.update_moderation_words(
        UpdateModerationWordsRequest(profanityWords=["Fresh"]),
        update_db,
    )
    assert updated == {"profanityWords": ["fresh"]}
    assert existing_config.profanity_words == ["fresh"]


@pytest.mark.asyncio
async def test_search_universities_and_academic_interests(mock_db, scalar_result):
    university = SimpleNamespace(
        id=uuid4(),
        name="Kampu University",
        slug="kampu",
        website=None,
        major="Computer Science",
        minor="Design",
        academic_program="BS",
    )
    db = mock_db(scalar_result(1), scalar_result(values=[(university, "India")]))

    result = await search_universities(UniversitySearchParams(query="kampu", page=1, pageSize=10), db)
    assert result["query"] == "kampu"
    assert result["items"][0]["country"] == "India"
    assert result["totalItems"] == 1

    interest = SimpleNamespace(id=1, name="AI")
    db = mock_db(scalar_result(1), scalar_result(values=[interest]))
    interests = await get_academic_interests("a", 1, 10, db)
    assert interests["items"] == [{"id": "1", "name": "AI"}]


    bachelors = SimpleNamespace(id=1, name="Bachelors", is_active=True)
    masters = SimpleNamespace(id=2, name="Masters", is_active=True)
    chemistry = SimpleNamespace(id=5, name="Chemistry", education_level_id=1, is_active=True)
    ai = SimpleNamespace(id=1, name="Artificial Intelligence", education_level_id=2, is_active=True)

    # education levels + interests + countries + hashtags
    db = mock_db(
        scalar_result(values=[bachelors, masters]),
        scalar_result(values=[chemistry, ai]),
        scalar_result(0),
        scalar_result(values=[]),
        scalar_result(0),
        scalar_result(values=[]),
    )
    info = await get_academics_info(None, 1, 10, db)
    assert info["educationLevels"] == [
        {
            "id": "1",
            "name": "Bachelors",
            "interests": [{"id": "5", "name": "Chemistry"}],
        },
        {
            "id": "2",
            "name": "Masters",
            "interests": [{"id": "1", "name": "Artificial Intelligence"}],
        },
    ]
    assert "interests" not in info
    assert info["countries"]["items"] == []
    assert info["countries"]["totalItems"] == 0
    assert info["hashtags"]["items"] == []
    assert info["hashtags"]["totalItems"] == 0


@pytest.mark.asyncio
async def test_create_academic_interest(mock_db, scalar_result):
    education_level = SimpleNamespace(id=1, name="Bachelors", is_active=True)
    db = mock_db(scalar_result(education_level), scalar_result(None))

    async def set_generated_id(interest):
        interest.id = 9

    db.refresh.side_effect = set_generated_id

    result = await create_academic_interest("Data Science", 1, db)

    assert result == {
        "id": "9",
        "name": "Data Science",
        "educationLevelId": "1",
        "isActive": True,
    }
    db.add.assert_called_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_completeness_score_paths(monkeypatch, mock_db, scalar_result):
    user_id = uuid4()
    user = SimpleNamespace(id=user_id, email="student@example.test")
    full_profile = Profile(
        user_id=user_id,
        first_name="A",
        last_name="Student",
        bio="bio",
        university_id=uuid4(),
        major="CS",
        edu_level="Bachelors",
        profile_photo_url="profiles/a.png",
        profile_interests_id=[1, 2],
        graduation_date=date(2027, 5, 1),
        location_text="Campus",
    )
    weights = CompletenessWeight()
    monkeypatch.setattr(completeness_service, "get_completeness_weights", lambda db: weights)

    async def async_weights(db):
        return weights

    monkeypatch.setattr(completeness_service, "get_completeness_weights", async_weights)
    db = mock_db(scalar_result(user), scalar_result(full_profile))
    assert await completeness_service.calculate_completeness_score(user_id, db) == 100

    db = mock_db(scalar_result(None))
    assert await completeness_service.calculate_completeness_score(user_id, db) == 0

    zero_weights = CompletenessWeight(
        bio=0,
        university=0,
        major=0,
        edu_level=0,
        first_name=0,
        last_name=0,
        email=0,
        profile_photo_url=0,
        interests=0,
        graduation_date=0,
        location=0,
    )

    async def async_zero_weights(db):
        return zero_weights

    monkeypatch.setattr(completeness_service, "get_completeness_weights", async_zero_weights)
    db = mock_db(scalar_result(user), scalar_result(full_profile))
    assert await completeness_service.calculate_completeness_score(user_id, db) == 0


@pytest.mark.asyncio
async def test_update_completeness_weights_recalculates_profiles(monkeypatch, mock_db, scalar_result):
    weights = CompletenessWeight()
    profile = SimpleNamespace(user_id=uuid4(), completeness_score=0)

    async def fake_score(user_id, db):
        return 77

    monkeypatch.setattr(completeness_service, "get_completeness_weights", lambda db: weights)

    async def async_weights(db):
        return weights

    monkeypatch.setattr(completeness_service, "get_completeness_weights", async_weights)
    monkeypatch.setattr(completeness_service, "calculate_completeness_score", fake_score)

    payload = SimpleNamespace(model_dump=lambda exclude_unset=True: {"bio": 20, "email": None})
    db = mock_db(scalar_result(values=[profile]))

    result = await completeness_service.update_completeness_weights(payload, db)
    assert result["weights"]["bio"] == 20
    assert result["weights"]["email"] == 10
    assert profile.completeness_score == 77
    assert db.commit.await_count == 2
