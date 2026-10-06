from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import apps.feed.services  # noqa: F401
from apps.feed.repositories import post_repository
from apps.engagement.repositories import repost_repository
from apps.profiles.services import response_service as rs


@pytest.mark.asyncio
async def test_resolve_posts_count_visitor_uses_live_public_count():
    db = AsyncMock()
    profile_user_id = uuid.uuid4()
    viewer_id = uuid.uuid4()

    with patch(
        "apps.profiles.services.profile_stats_service.count_public_posts_for_user",
        AsyncMock(return_value=3),
    ) as count_fn:
        count = await rs._resolve_posts_count(
            db,
            profile_user_id=profile_user_id,
            cached_posts_count=4,
            viewer_user_id=viewer_id,
        )

    assert count == 3
    count_fn.assert_awaited_once_with(db, profile_user_id)


@pytest.mark.asyncio
async def test_resolve_posts_count_owner_includes_flagged():
    db = AsyncMock()
    profile_user_id = uuid.uuid4()

    with patch.object(
        rs,
        "_count_owner_visible_posts",
        AsyncMock(return_value=6),
    ) as count_fn:
        count = await rs._resolve_posts_count(
            db,
            profile_user_id=profile_user_id,
            cached_posts_count=4,
            viewer_user_id=profile_user_id,
        )

    assert count == 6
    count_fn.assert_awaited_once_with(db, profile_user_id)


@pytest.mark.asyncio
async def test_count_owner_visible_posts_includes_active_reposts():
    db = AsyncMock()
    user_id = uuid.uuid4()

    with (
        patch.object(
            post_repository,
            "count_posts_by_state",
            AsyncMock(return_value=2),
        ) as authored_fn,
        patch.object(
            repost_repository,
            "count_active_reposts_for_user",
            AsyncMock(return_value=1),
        ) as reposts_fn,
    ):
        count = await rs._count_owner_visible_posts(db, user_id)

    assert count == 3
    authored_fn.assert_awaited_once()
    reposts_fn.assert_awaited_once_with(db, user_id)


@pytest.mark.asyncio
async def test_build_user_base_response_resolves_numeric_major_and_name_minor():
    from types import SimpleNamespace
    from common.enums import RegistrationType, UserStatus, Role

    user = SimpleNamespace(
        id=uuid.uuid4(),
        email="test@example.com",
        role="user",
        registration_type=RegistrationType.email,
        status=UserStatus.active,
        email_verified_at=None,
        onboarding_status=SimpleNamespace(value="completed"),
        is_deleted=False,
        created_at=None,
        updated_at=None,
    )
    profile = SimpleNamespace(
        id=uuid.uuid4(),
        first_name="Sahil",
        last_name="Deshpande",
        profile_photo_url=None,
        banner_photo_url=None,
        university_id=None,
        major="1",
        minor="Psychology",
        major_id=None,
        minor_id=None,
        country_id=None,
        edu_level=None,
        bio="Bio",
        profile_interests_id=[],
        graduation_date=None,
        location_text=None,
        profile_visibility="private",
        completeness_score=80,
        online_presence_visible=True,
        posts_count=5,
        followers_count=0,
        following_count=0,
    )

    db = AsyncMock()
    major_mock_row = SimpleNamespace(id=1, name="Computer Science")
    minor_mock_row = SimpleNamespace(id=5, name="Psychology")

    db.execute.side_effect = [
        SimpleNamespace(first=lambda: major_mock_row),
        SimpleNamespace(first=lambda: minor_mock_row),
    ]

    with patch(
        "apps.profiles.services.profile_stats_service.get_connection_count_for_profile",
        AsyncMock(return_value=4),
    ), patch.object(
        rs,
        "_resolve_posts_count",
        AsyncMock(return_value=5),
    ):
        result = await rs.build_user_base_response(user, profile, db)

    assert result["major"] == "Computer Science"
    assert result["major_details"] == {
        "id": 1,
        "major_name": "Computer Science",
    }
    assert result["minor"] == "Psychology"
    assert result["minor_details"] == {
        "id": 5,
        "minor_name": "Psychology",
    }


@pytest.mark.asyncio
async def test_build_user_base_response_with_major_id_and_minor_id():
    from types import SimpleNamespace
    from common.enums import RegistrationType, UserStatus, Role

    user = SimpleNamespace(
        id=uuid.uuid4(),
        email="test@example.com",
        role="user",
        registration_type=RegistrationType.email,
        status=UserStatus.active,
        email_verified_at=None,
        onboarding_status=SimpleNamespace(value="completed"),
        is_deleted=False,
        created_at=None,
        updated_at=None,
    )
    profile = SimpleNamespace(
        id=uuid.uuid4(),
        first_name="Jane",
        last_name="Doe",
        profile_photo_url=None,
        banner_photo_url=None,
        university_id=None,
        major=None,
        minor=None,
        major_id=2,
        minor_id=3,
        country_id=None,
        edu_level=None,
        bio=None,
        profile_interests_id=None,
        graduation_date=None,
        location_text=None,
        profile_visibility="public",
        completeness_score=50,
        online_presence_visible=True,
        posts_count=0,
        followers_count=0,
        following_count=0,
    )

    db = AsyncMock()
    catalog_row = SimpleNamespace(
        university_name=None,
        university_website=None,
        country_name=None,
        major_id=2,
        major_name="Electrical Engineering",
        minor_id=3,
        minor_name="Mathematics",
    )
    db.execute.side_effect = [
        SimpleNamespace(first=lambda: catalog_row),
    ]

    with patch(
        "apps.profiles.services.profile_stats_service.get_connection_count_for_profile",
        AsyncMock(return_value=0),
    ), patch.object(
        rs,
        "_resolve_posts_count",
        AsyncMock(return_value=0),
    ):
        result = await rs.build_user_base_response(user, profile, db)

    assert result["major"] == "Electrical Engineering"
    assert result["major_details"] == {
        "id": 2,
        "major_name": "Electrical Engineering",
    }
    assert result["minor"] == "Mathematics"
    assert result["minor_details"] == {
        "id": 3,
        "minor_name": "Mathematics",
    }


@pytest.mark.asyncio
async def test_build_user_base_response_none_profile():
    from types import SimpleNamespace
    from common.enums import RegistrationType, UserStatus, Role

    user = SimpleNamespace(
        id=uuid.uuid4(),
        email="test@example.com",
        role="user",
        registration_type=RegistrationType.email,
        status=UserStatus.active,
        email_verified_at=None,
        onboarding_status=SimpleNamespace(value="completed"),
        is_deleted=False,
        created_at=None,
        updated_at=None,
    )

    db = AsyncMock()

    with patch(
        "apps.profiles.services.profile_stats_service.get_connection_count_for_profile",
        AsyncMock(return_value=0),
    ), patch.object(
        rs,
        "_resolve_posts_count",
        AsyncMock(return_value=0),
    ):
        result = await rs.build_user_base_response(user, None, db)

    assert result["major"] is None
    assert result["major_details"] == {"id": None, "major_name": None}
    assert result["minor"] is None
    assert result["minor_details"] == {"id": None, "minor_name": None}


@pytest.mark.asyncio
async def test_build_user_base_response_resolves_university_website():
    from types import SimpleNamespace
    from common.enums import RegistrationType, UserStatus

    uni_id = uuid.uuid4()
    user = SimpleNamespace(
        id=uuid.uuid4(),
        email="test@example.com",
        role="user",
        registration_type=RegistrationType.email,
        status=UserStatus.active,
        email_verified_at=None,
        onboarding_status=SimpleNamespace(value="completed"),
        is_deleted=False,
        created_at=None,
        updated_at=None,
    )
    profile = SimpleNamespace(
        id=uuid.uuid4(),
        first_name="Sahil",
        last_name="Deshpande",
        profile_photo_url=None,
        banner_photo_url=None,
        university_id=uni_id,
        major=None,
        minor=None,
        major_id=None,
        minor_id=None,
        country_id=None,
        edu_level=None,
        bio=None,
        profile_interests_id=None,
        graduation_date=None,
        location_text=None,
        profile_visibility="public",
        completeness_score=50,
        online_presence_visible=True,
        posts_count=0,
        followers_count=0,
        following_count=0,
    )

    db = AsyncMock()
    catalog_row = SimpleNamespace(
        university_name="Indian Institute of Technology Bombay",
        university_website="https://www.iitb.ac.in",
        country_name=None,
        major_id=None,
        major_name=None,
        minor_id=None,
        minor_name=None,
    )
    db.execute.side_effect = [
        SimpleNamespace(first=lambda: catalog_row),
    ]

    with patch(
        "apps.profiles.services.profile_stats_service.get_connection_count_for_profile",
        AsyncMock(return_value=0),
    ), patch.object(
        rs,
        "_resolve_posts_count",
        AsyncMock(return_value=0),
    ):
        result = await rs.build_user_base_response(user, profile, db)

    assert result["university"] == "Indian Institute of Technology Bombay"
    assert result["university_details"] == {
        "id": uni_id,
        "university_name": "Indian Institute of Technology Bombay",
        "university_website": "https://www.iitb.ac.in",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw_status, expected_display",
    [
        ("active", "Active"),
        ("suspicious_review", "Suspicious_review"),
        ("suspended", "Suspended"),
        ("banned", "Banned"),
        ("deleting", "Deleting"),
        ("pending", "Pending"),
    ],
)
async def test_build_user_base_response_formats_user_status(raw_status, expected_display):
    from common.enums import UserStatus, RegistrationType

    user = SimpleNamespace(
        id=uuid.uuid4(),
        email="test@example.com",
        role="user",
        registration_type=RegistrationType.email,
        status=UserStatus(raw_status),
        email_verified_at=None,
        created_at=None,
        updated_at=None,
        is_deleted=False,
    )
    db = AsyncMock()

    with patch(
        "apps.profiles.services.profile_stats_service.get_connection_count_for_profile",
        AsyncMock(return_value=0),
    ), patch.object(
        rs,
        "_resolve_posts_count",
        AsyncMock(return_value=0),
    ):
        result = await rs.build_user_base_response(user, None, db)

    assert result["status"] == expected_display


