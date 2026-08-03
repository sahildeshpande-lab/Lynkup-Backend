from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from apps.notifications.services.topic_service import (
    TopicService,
    format_topic,
    slugify_topic_value,
)


def test_slugify_topic_value_normalizes_text() -> None:
    assert slugify_topic_value("Computer Science") == "computer_science"
    assert slugify_topic_value("  Artificial Intelligence!! ") == "artificial_intelligence"
    assert slugify_topic_value("") == ""


def test_format_topic_builds_prefixed_slug() -> None:
    assert format_topic("university", "MIT") == "university_mit"
    assert format_topic("major", "Computer Science") == "major_computer_science"
    assert format_topic("interest", "Machine Learning") == "interest_machine_learning"
    assert format_topic("minor", "   ") is None


@pytest.mark.asyncio
async def test_resolve_education_level_target_id_to_label(mock_db) -> None:
    from apps.notifications.services import topic_service as ts
    from common.enums import NotificationTargetType

    resolved = await ts._resolve_target_value_for_topic(
        mock_db(),
        NotificationTargetType.education_level,
        "1",
    )
    assert resolved == "Bachelors"
    assert format_topic("education_level", resolved) == "education_level_bachelors"


@pytest.mark.asyncio
async def test_topics_from_education_level(mock_db) -> None:
    from apps.notifications.services import topic_service as ts

    profile = SimpleNamespace(user_id=uuid4(), edu_level="Bachelors")
    assert await ts._topics_from_education_level(mock_db(), profile) == {
        "education_level_bachelors"
    }


@pytest.mark.asyncio
async def test_resolve_hashtag_target_uuid_to_tag(mock_db, scalar_result) -> None:
    from apps.notifications.services import topic_service as ts
    from common.enums import NotificationTargetType

    hashtag_id = uuid4()
    hashtag = SimpleNamespace(id=hashtag_id, tag="machinelearning")
    db = mock_db(scalar_result(hashtag))

    resolved = await ts._resolve_target_value_for_topic(
        db,
        NotificationTargetType.hashtags,
        str(hashtag_id),
    )
    assert resolved == "machinelearning"
    assert format_topic("hashtag", resolved) == "hashtag_machinelearning"


@pytest.mark.asyncio
async def test_resolve_hashtag_target_accepts_tag_name(mock_db) -> None:
    from apps.notifications.services import topic_service as ts
    from common.enums import NotificationTargetType

    resolved = await ts._resolve_target_value_for_topic(
        mock_db(),
        NotificationTargetType.hashtags,
        "#FastAPI",
    )
    assert resolved == "fastapi"


@pytest.mark.asyncio
async def test_topics_from_hashtags_uses_published_posts(mock_db) -> None:
    from apps.notifications.services import topic_service as ts

    profile = SimpleNamespace(user_id=uuid4())
    db = mock_db()
    db.execute = AsyncMock(
        return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: ["ai", "ml"]))
    )

    topics = await ts._topics_from_hashtags(db, profile)
    assert topics == {"hashtag_ai", "hashtag_ml"}


@pytest.mark.asyncio
async def test_build_topics_combines_registered_builders(mock_db) -> None:
    db = mock_db()
    profile = SimpleNamespace(user_id=uuid4())

    from apps.notifications.services import topic_service as ts

    original = list(ts._TOPIC_BUILDERS)
    ts._TOPIC_BUILDERS[:] = [
        AsyncMock(return_value={"university_mit"}),
        AsyncMock(return_value={"major_computer_science"}),
        AsyncMock(return_value={"minor_data_science"}),
        AsyncMock(return_value={"education_level_bachelors"}),
        AsyncMock(
            return_value={
                "interest_artificial_intelligence",
                "interest_machine_learning",
            }
        ),
        AsyncMock(return_value={"hashtag_ai"}),
    ]
    try:
        topics = await TopicService.build_topics(db, profile)
    finally:
        ts._TOPIC_BUILDERS[:] = original

    assert topics == {
        "university_mit",
        "major_computer_science",
        "minor_data_science",
        "education_level_bachelors",
        "interest_artificial_intelligence",
        "interest_machine_learning",
        "hashtag_ai",
    }


@pytest.mark.asyncio
async def test_sync_topics_subscribes_and_unsubscribes_diff_only(mock_db) -> None:
    db = mock_db()
    user_id = uuid4()
    tokens = ["token-a", "token-b"]
    old_topics = {"university_mit", "major_computer_science"}
    new_topics = {"university_stanford", "major_computer_science", "interest_ai"}

    with (
        patch(
            "apps.notifications.services.topic_service.get_active_fcm_tokens_for_users",
            AsyncMock(return_value=tokens),
        ) as tokens_mock,
        patch.object(TopicService, "subscribe", return_value={"successful_count": 2}) as sub,
        patch.object(TopicService, "unsubscribe", return_value={"successful_count": 1}) as unsub,
    ):
        result = await TopicService.sync_topics(
            db,
            user_id,
            old_topics=old_topics,
            new_topics=new_topics,
        )

    tokens_mock.assert_awaited_once_with(db, [user_id])
    sub.assert_called_once()
    unsub.assert_called_once()

    subscribed = sub.call_args.args[1]
    unsubscribed = unsub.call_args.args[1]
    assert subscribed == {"university_stanford", "interest_ai"}
    assert unsubscribed == {"university_mit"}
    assert "major_computer_science" not in subscribed
    assert "major_computer_science" not in unsubscribed
    assert result["token_count"] == 2


@pytest.mark.asyncio
async def test_sync_topics_no_op_when_unchanged(mock_db) -> None:
    db = mock_db()
    topics = {"university_mit", "major_cs"}

    with (
        patch(
            "apps.notifications.services.topic_service.get_active_fcm_tokens_for_users",
            AsyncMock(),
        ) as tokens_mock,
        patch.object(TopicService, "subscribe") as sub,
        patch.object(TopicService, "unsubscribe") as unsub,
    ):
        result = await TopicService.sync_topics(
            db,
            uuid4(),
            old_topics=topics,
            new_topics=set(topics),
        )

    tokens_mock.assert_not_awaited()
    sub.assert_not_called()
    unsub.assert_not_called()
    assert result["token_count"] == 0


def test_subscribe_continues_after_topic_failure() -> None:
    response_ok = SimpleNamespace(success_count=2, failure_count=0)

    with (
        patch(
            "apps.notifications.services.topic_service.initialize_firebase_app",
        ),
        patch(
            "apps.notifications.services.topic_service.messaging.subscribe_to_topic",
            side_effect=[RuntimeError("boom"), response_ok],
        ) as api,
    ):
        result = TopicService.subscribe(
            ["token-1", "token-2"],
            {"topic_a", "topic_b"},
        )

    assert api.call_count == 2
    assert result["successful_count"] == 1
    assert result["failed_count"] == 1


@pytest.mark.asyncio
async def test_topics_from_university_uses_slug(mock_db, scalar_result) -> None:
    from apps.notifications.services import topic_service as ts

    university_id = uuid4()
    profile = SimpleNamespace(user_id=uuid4(), university_id=university_id)
    university = SimpleNamespace(
        id=university_id,
        slug="mit",
        name="Massachusetts Institute of Technology",
    )
    db = mock_db(scalar_result(university))

    topics = await ts._topics_from_university(db, profile)
    assert topics == {"university_mit"}


@pytest.mark.asyncio
async def test_topics_from_major_and_minor(mock_db) -> None:
    from apps.notifications.services import topic_service as ts

    profile = SimpleNamespace(
        major="Computer Science",
        minor="Data Science",
    )
    assert await ts._topics_from_major(mock_db(), profile) == {"major_computer_science"}
    assert await ts._topics_from_minor(mock_db(), profile) == {"minor_data_science"}


@pytest.mark.asyncio
async def test_topics_from_country_uses_country_name(mock_db, scalar_result) -> None:
    from apps.notifications.services import topic_service as ts

    country_id = uuid4()
    profile = SimpleNamespace(user_id=uuid4(), country_id=country_id)
    country = SimpleNamespace(id=country_id, name="United States")
    db = mock_db(scalar_result(country))

    topics = await ts._topics_from_country(db, profile)
    assert topics == {"country_united_states"}


@pytest.mark.asyncio
async def test_affects_topics_includes_country_id() -> None:
    payload = SimpleNamespace(
        major=None,
        minor=None,
        university_id=None,
        country_id=uuid4(),
        education_level_id=None,
        academic_interests=None,
    )
    assert TopicService.affects_topics(payload) is True


@pytest.mark.asyncio
async def test_update_profile_syncs_topic_diff(mock_db) -> None:
    from apps.profiles.schemas import UpdateProfileRequest
    from apps.profiles.services import profile_service as svc

    db = mock_db()
    user = SimpleNamespace(id=uuid4())
    profile = SimpleNamespace(
        user_id=user.id,
        first_name="A",
        last_name="B",
        major="Old Major",
        minor=None,
        bio=None,
        university_id=None,
        edu_level=None,
        profile_interests_id=[],
        profile_photo_url=None,
        banner_photo_url=None,
        completeness_score=0,
    )
    payload = UpdateProfileRequest(major="New Major")

    with (
        patch.object(
            db,
            "execute",
            AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: profile)),
        ),
        patch.object(svc, "calculate_completeness_score", AsyncMock(return_value=70)),
        patch.object(svc, "get_my_profile_service", AsyncMock(return_value={"user": {}})),
        patch(
            "apps.notifications.services.topic_service.TopicService.capture_topics",
            AsyncMock(return_value={"major_old_major"}),
        ),
        patch(
            "apps.notifications.services.topic_service.TopicService.sync_user_topics",
            AsyncMock(return_value={}),
        ) as sync,
    ):
        await svc.update_my_profile_service(user=user, payload=payload, db=db)

    sync.assert_awaited_once()
    assert sync.await_args.kwargs["old_topics"] == {"major_old_major"}
    assert sync.await_args.kwargs["profile"] is profile


@pytest.mark.asyncio
async def test_update_profile_skips_topic_sync_when_unrelated_fields_change(mock_db) -> None:
    from apps.profiles.schemas import UpdateProfileRequest
    from apps.profiles.services import profile_service as svc

    db = mock_db()
    user = SimpleNamespace(id=uuid4())
    profile = SimpleNamespace(
        user_id=user.id,
        first_name="A",
        last_name="B",
        major="CS",
        minor=None,
        bio=None,
        university_id=None,
        edu_level=None,
        profile_interests_id=[],
        profile_photo_url=None,
        banner_photo_url=None,
        completeness_score=0,
    )
    payload = UpdateProfileRequest(bio="only bio")

    with (
        patch.object(
            db,
            "execute",
            AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: profile)),
        ),
        patch.object(svc, "calculate_completeness_score", AsyncMock(return_value=70)),
        patch.object(svc, "get_my_profile_service", AsyncMock(return_value={"user": {}})),
        patch(
            "apps.notifications.services.topic_service.TopicService.sync_user_topics",
            AsyncMock(),
        ) as sync,
    ):
        await svc.update_my_profile_service(user=user, payload=payload, db=db)

    sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_profile_continues_when_topic_sync_fails(mock_db) -> None:
    from apps.profiles.schemas import UpdateProfileRequest
    from apps.profiles.services import profile_service as svc

    db = mock_db()
    user = SimpleNamespace(id=uuid4())
    profile = SimpleNamespace(
        user_id=user.id,
        first_name="A",
        last_name="B",
        major="CS",
        minor=None,
        bio=None,
        university_id=None,
        edu_level=None,
        profile_interests_id=[],
        profile_photo_url=None,
        banner_photo_url=None,
        completeness_score=0,
    )
    payload = UpdateProfileRequest(major="Math")

    with (
        patch.object(
            db,
            "execute",
            AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: profile)),
        ),
        patch.object(svc, "calculate_completeness_score", AsyncMock(return_value=70)),
        patch.object(
            svc,
            "get_my_profile_service",
            AsyncMock(return_value={"user": {"ok": True}}),
        ),
        patch(
            "apps.notifications.services.topic_service.TopicService.capture_topics",
            AsyncMock(return_value={"major_cs"}),
        ),
        patch(
            "apps.notifications.services.topic_service.TopicService.sync_user_topics",
            AsyncMock(return_value={}),
        ),
    ):
        result = await svc.update_my_profile_service(user=user, payload=payload, db=db)

    assert result == {"user": {"ok": True}}


@pytest.mark.asyncio
async def test_admin_update_profile_syncs_topics(mock_db) -> None:
    from apps.profiles.schemas import UpdateProfileRequest
    from apps.profiles.services import profile_service as svc

    db = mock_db()
    user_id = uuid4()
    user = SimpleNamespace(id=user_id)
    profile = SimpleNamespace(
        user_id=user_id,
        first_name="A",
        last_name="B",
        major="Old Major",
        minor=None,
        bio=None,
        university_id=None,
        edu_level=None,
        profile_interests_id=[],
        profile_photo_url=None,
        banner_photo_url=None,
        completeness_score=0,
    )
    payload = UpdateProfileRequest(major="New Major", minor="Stats")

    execute_results = [
        SimpleNamespace(scalar_one_or_none=lambda: user),
        SimpleNamespace(scalar_one_or_none=lambda: profile),
    ]

    async def _execute(_statement):
        if execute_results:
            return execute_results.pop(0)
        return SimpleNamespace(scalar_one_or_none=lambda: profile)

    db.execute = AsyncMock(side_effect=_execute)

    with (
        patch.object(svc, "calculate_completeness_score", AsyncMock(return_value=70)),
        patch.object(svc, "get_my_profile_service", AsyncMock(return_value={"user": {}})),
        patch(
            "apps.notifications.services.topic_service.TopicService.capture_topics",
            AsyncMock(return_value={"major_old_major"}),
        ) as capture,
        patch(
            "apps.notifications.services.topic_service.TopicService.sync_user_topics",
            AsyncMock(return_value={}),
        ) as sync,
    ):
        await svc.update_user_profile_by_admin_service(
            user_id=user_id,
            payload=payload,
            db=db,
        )

    capture.assert_awaited_once()
    sync.assert_awaited_once()
    assert sync.await_args.args[1] == user_id
    assert sync.await_args.kwargs["old_topics"] == {"major_old_major"}
    assert sync.await_args.kwargs["profile"] is profile


@pytest.mark.asyncio
async def test_sync_user_topics_builds_new_and_diffs(mock_db) -> None:
    db = mock_db()
    user_id = uuid4()
    profile = SimpleNamespace(user_id=user_id)

    with (
        patch.object(
            TopicService,
            "build_topics",
            AsyncMock(return_value={"university_stanford", "major_cs"}),
        ),
        patch.object(
            TopicService,
            "sync_topics",
            AsyncMock(return_value={"token_count": 1}),
        ) as sync,
    ):
        result = await TopicService.sync_user_topics(
            db,
            user_id,
            old_topics={"university_mit", "major_cs"},
            profile=profile,
        )

    sync.assert_awaited_once_with(
        db,
        user_id,
        old_topics={"university_mit", "major_cs"},
        new_topics={"university_stanford", "major_cs"},
    )
    assert result["token_count"] == 1
