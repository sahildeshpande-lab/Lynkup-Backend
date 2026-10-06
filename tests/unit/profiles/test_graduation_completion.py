from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from apps.accounts.db_models import User
from apps.profiles.db_models import Profile
from apps.profiles.graduation_date import (
    format_graduation_date,
    parse_graduation_date,
)
from apps.profiles.schemas import OnboardingRequest
from apps.profiles.services.response_service import (
    apply_profile_graduation_date,
    build_user_base_response,
    is_graduation_completed,
)
from common.enums import UserStatus
from core.email_service import build_graduation_completion_email_html


@pytest.mark.parametrize(
    ("graduation_date", "reference_date", "expected"),
    [
        (None, date(2026, 9, 1), False),
        (date(2026, 9, 1), date(2026, 8, 31), False),
        (date(2026, 9, 1), date(2026, 9, 1), False),
        (date(2026, 9, 1), date(2026, 9, 2), True),
        (date(2026, 9, 1), date(2026, 9, 1) + timedelta(days=1), True),
    ],
)
def test_is_graduation_completed(
    graduation_date: date | None,
    reference_date: date,
    expected: bool,
) -> None:
    assert (
        is_graduation_completed(graduation_date, reference_date=reference_date)
        is expected
    )


@pytest.mark.asyncio
async def test_build_user_base_response_includes_graduation_fields(monkeypatch) -> None:
    user = User(
        id=uuid4(),
        email="graduation@example.com",
        firebase_uid="firebase-uid",
        status=UserStatus.active,
    )
    profile = Profile(
        user_id=user.id,
        first_name="Grad",
        last_name="Student",
        graduation_date=date(2020, 1, 1),
        is_alumni=True,
    )

    monkeypatch.setattr(
        "apps.profiles.services.profile_stats_service.get_connection_count_for_profile",
        AsyncMock(return_value=0),
    )
    monkeypatch.setattr(
        "apps.profiles.services.response_service._resolve_posts_count",
        AsyncMock(return_value=0),
    )

    response = await build_user_base_response(
        user,
        profile,
        AsyncMock(),
        viewer_user_id=user.id,
    )

    assert response["graduationDate"] == "01-01-2020"
    assert response["is_graduation_completed"] is True
    assert response["is_alumni"] is True
    assert response["has_changed_email_after_graduation"] is False


@pytest.mark.asyncio
async def test_build_user_base_response_graduation_null_is_not_completed(monkeypatch) -> None:
    user = User(
        id=uuid4(),
        email="nograd@example.com",
        firebase_uid="firebase-uid",
        status=UserStatus.active,
    )
    profile = Profile(user_id=user.id, first_name="No", last_name="Grad")

    monkeypatch.setattr(
        "apps.profiles.services.profile_stats_service.get_connection_count_for_profile",
        AsyncMock(return_value=0),
    )
    monkeypatch.setattr(
        "apps.profiles.services.response_service._resolve_posts_count",
        AsyncMock(return_value=0),
    )

    response = await build_user_base_response(
        user,
        profile,
        AsyncMock(),
        viewer_user_id=user.id,
    )

    assert response["graduationDate"] is None
    assert response["is_graduation_completed"] is False
    assert response["is_alumni"] is None
    assert response["has_changed_email_after_graduation"] is False


@pytest.mark.asyncio
async def test_build_user_base_response_has_changed_email_after_graduation_true(monkeypatch) -> None:
    user = User(
        id=uuid4(),
        email="changed@example.com",
        firebase_uid="firebase-uid",
        status=UserStatus.active,
        has_changed_email_after_graduation=True,
    )
    profile = Profile(user_id=user.id, first_name="Changed", last_name="User")

    monkeypatch.setattr(
        "apps.profiles.services.profile_stats_service.get_connection_count_for_profile",
        AsyncMock(return_value=0),
    )
    monkeypatch.setattr(
        "apps.profiles.services.response_service._resolve_posts_count",
        AsyncMock(return_value=0),
    )

    response = await build_user_base_response(
        user,
        profile,
        AsyncMock(),
        viewer_user_id=user.id,
    )

    assert response["has_changed_email_after_graduation"] is True


def test_onboarding_request_accepts_optional_graduation_date() -> None:
    payload = OnboardingRequest(
        university_id="11111111-1111-1111-1111-111111111111",
        major="Physics",
        education_level_id=2,
        academic_interests=["Math"],
    )
    assert payload.graduationDate is None


def test_onboarding_request_accepts_valid_graduation_date_string() -> None:
    payload = OnboardingRequest(
        university_id="11111111-1111-1111-1111-111111111111",
        major="Physics",
        education_level_id=2,
        academic_interests=["Math"],
        graduationDate="15-05-2027",
    )
    assert payload.graduationDate == date(2027, 5, 15)


def test_onboarding_request_accepts_iso_graduation_date_for_backward_compat() -> None:
    payload = OnboardingRequest(
        university_id="11111111-1111-1111-1111-111111111111",
        major="Physics",
        education_level_id=2,
        academic_interests=["Math"],
        graduationDate="2027-05-15",
    )
    assert payload.graduationDate == date(2027, 5, 15)


def test_format_and_parse_graduation_date_helpers() -> None:
    value = date(2027, 5, 15)
    assert format_graduation_date(value) == "15-05-2027"
    assert parse_graduation_date("15-05-2027") == value
    assert parse_graduation_date("2027-05-15") == value
    assert format_graduation_date(None) is None
    assert parse_graduation_date(None) is None


def test_onboarding_request_accepts_valid_graduation_date() -> None:
    payload = OnboardingRequest(
        university_id="11111111-1111-1111-1111-111111111111",
        major="Physics",
        education_level_id=2,
        academic_interests=["Math"],
        graduationDate=date(2027, 5, 15),
    )
    assert payload.graduationDate == date(2027, 5, 15)


def test_onboarding_request_rejects_invalid_graduation_date() -> None:
    with pytest.raises(ValidationError):
        OnboardingRequest(
            university_id="11111111-1111-1111-1111-111111111111",
            major="Physics",
            education_level_id=2,
            academic_interests=["Math"],
            graduationDate="not-a-date",
        )


def _onboarding_kwargs(**overrides):
    payload = {
        "university_id": "11111111-1111-1111-1111-111111111111",
        "major": "Physics",
        "education_level_id": 2,
        "academic_interests": ["Math"],
    }
    payload.update(overrides)
    return payload


def test_onboarding_request_accepts_today_graduation_date() -> None:
    today = datetime.now(timezone.utc).date()
    payload = OnboardingRequest(**_onboarding_kwargs(graduationDate=today))
    assert payload.graduationDate == today


def test_onboarding_request_accepts_yesterday_graduation_date() -> None:
    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    payload = OnboardingRequest(**_onboarding_kwargs(graduationDate=yesterday))
    assert payload.graduationDate == yesterday


def test_onboarding_request_accepts_past_graduation_date() -> None:
    two_days_ago = datetime.now(timezone.utc).date() - timedelta(days=2)
    payload = OnboardingRequest(**_onboarding_kwargs(graduationDate=two_days_ago))
    assert payload.graduationDate == two_days_ago


def test_update_profile_request_accepts_past_graduation_date() -> None:
    from apps.profiles.schemas import UpdateProfileRequest

    two_days_ago = datetime.now(timezone.utc).date() - timedelta(days=2)
    payload = UpdateProfileRequest(graduationDate=two_days_ago)
    assert payload.graduationDate == two_days_ago


@pytest.mark.asyncio
async def test_complete_onboarding_saves_graduation_date_when_provided(monkeypatch) -> None:
    from apps.profiles.services.onboarding_service import complete_onboarding

    user = MagicMock()
    user.id = uuid4()
    user.email = "grad-onboard@example.com"
    user.onboarding_status = None
    user.status = UserStatus.pending

    profile = Profile(user_id=user.id, first_name="", last_name="", completeness_score=0)
    graduation = date(2027, 5, 15)

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(
        side_effect=[
            MagicMock(scalar_one_or_none=MagicMock(return_value=profile)),
        ]
    )
    mock_db.refresh = AsyncMock()

    monkeypatch.setattr("core.images.file_exists", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        "apps.profiles.services.onboarding_service._resolve_catalog_program",
        AsyncMock(side_effect=[(1, "Physics"), (None, None)]),
    )
    monkeypatch.setattr(
        "apps.profiles.services.onboarding_service._resolve_academic_interest_ids",
        AsyncMock(return_value=[1]),
    )
    monkeypatch.setattr(
        "apps.profiles.services.onboarding_service.calculate_completeness_score",
        AsyncMock(return_value=80),
    )
    monkeypatch.setattr(
        "apps.recommendations.services.post_keyword_service.refresh_profile_extracted_keywords_best_effort",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "apps.chat.service.sync_stream_user_on_auth",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "apps.notifications.services.topic_service.TopicService.refresh_user_topic_subscriptions",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "apps.profiles.services.onboarding_service.build_user_base_response",
        AsyncMock(
            return_value={
                "graduationDate": format_graduation_date(graduation),
                "is_graduation_completed": False,
            }
        ),
    )

    result = await complete_onboarding(
        user=user,
        bio="Grad bio",
        major="Physics",
        minor=None,
        country_id=None,
        university_id=str(uuid4()),
        education_level_id=2,
        academic_interests=["Math"],
        profile_photo_key="profiles/test.png",
        banner_photo_key=None,
        db=mock_db,
        graduation_date=graduation,
    )

    assert profile.graduation_date == graduation
    assert profile.is_alumni is False
    assert result["user"]["graduationDate"] == "15-05-2027"
    assert result["user"]["is_graduation_completed"] is False
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_complete_onboarding_without_graduation_date_leaves_null(monkeypatch) -> None:
    from apps.profiles.services.onboarding_service import complete_onboarding

    user = MagicMock()
    user.id = uuid4()
    user.email = "no-grad-onboard@example.com"
    user.onboarding_status = None
    user.status = UserStatus.pending

    profile = Profile(user_id=user.id, first_name="", last_name="", completeness_score=0)

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(
        side_effect=[
            MagicMock(scalar_one_or_none=MagicMock(return_value=profile)),
        ]
    )
    mock_db.refresh = AsyncMock()

    monkeypatch.setattr("core.images.file_exists", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        "apps.profiles.services.onboarding_service._resolve_catalog_program",
        AsyncMock(side_effect=[(1, "Physics"), (None, None)]),
    )
    monkeypatch.setattr(
        "apps.profiles.services.onboarding_service._resolve_academic_interest_ids",
        AsyncMock(return_value=[1]),
    )
    monkeypatch.setattr(
        "apps.profiles.services.onboarding_service.calculate_completeness_score",
        AsyncMock(return_value=80),
    )
    monkeypatch.setattr(
        "apps.recommendations.services.post_keyword_service.refresh_profile_extracted_keywords_best_effort",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "apps.chat.service.sync_stream_user_on_auth",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "apps.notifications.services.topic_service.TopicService.refresh_user_topic_subscriptions",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "apps.profiles.services.onboarding_service.build_user_base_response",
        AsyncMock(
            return_value={
                "graduationDate": None,
                "is_graduation_completed": False,
            }
        ),
    )

    result = await complete_onboarding(
        user=user,
        bio="Bio",
        major="Physics",
        minor=None,
        country_id=None,
        university_id=str(uuid4()),
        education_level_id=2,
        academic_interests=["Math"],
        profile_photo_key="profiles/test.png",
        banner_photo_key=None,
        db=mock_db,
    )

    assert profile.graduation_date is None
    assert profile.is_alumni is None
    assert result["user"]["graduationDate"] is None
    assert result["user"]["is_graduation_completed"] is False
    mock_db.commit.assert_awaited_once()


def test_update_profile_request_accepts_optional_graduation_date() -> None:
    from apps.profiles.schemas import UpdateProfileRequest

    payload = UpdateProfileRequest(bio="Updated bio")
    assert payload.graduationDate is None


def test_update_profile_request_accepts_valid_graduation_date() -> None:
    from apps.profiles.schemas import UpdateProfileRequest

    payload = UpdateProfileRequest(graduationDate=date(2028, 6, 1))
    assert payload.graduationDate == date(2028, 6, 1)


@pytest.mark.asyncio
async def test_update_my_profile_service_saves_graduation_date(monkeypatch) -> None:
    from apps.profiles.schemas import UpdateProfileRequest
    from apps.profiles.services.profile_service import update_my_profile_service

    user = MagicMock()
    user.id = uuid4()
    user.email = "update-profile@example.com"

    profile = Profile(user_id=user.id, first_name="Test", last_name="User", completeness_score=0)
    graduation = date(2028, 3, 20)

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(
        side_effect=[
            MagicMock(scalar_one_or_none=MagicMock(return_value=profile)),
        ]
    )
    mock_db.refresh = AsyncMock()

    monkeypatch.setattr(
        "apps.profiles.services.profile_service.calculate_completeness_score",
        AsyncMock(return_value=75),
    )
    monkeypatch.setattr(
        "apps.recommendations.services.post_keyword_service.refresh_profile_extracted_keywords_best_effort",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "apps.profiles.services.profile_service.get_my_profile_service",
        AsyncMock(return_value={"user": {"graduationDate": format_graduation_date(graduation)}}),
    )

    payload = UpdateProfileRequest(graduationDate=graduation)
    result = await update_my_profile_service(user, payload, mock_db)

    assert profile.graduation_date == graduation
    assert profile.is_alumni is False
    assert result["user"]["graduationDate"] == "20-03-2028"
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_my_profile_service_resets_alumni_when_graduation_date_changes(
    monkeypatch,
) -> None:
    from apps.profiles.schemas import UpdateProfileRequest
    from apps.profiles.services.profile_service import update_my_profile_service

    user = MagicMock()
    user.id = uuid4()
    user.email = "update-profile@example.com"
    user.has_changed_email_after_graduation = True

    existing_graduation = date(2020, 1, 1)
    profile = Profile(
        user_id=user.id,
        first_name="Test",
        last_name="User",
        graduation_date=existing_graduation,
        is_alumni=True,
        completeness_score=0,
    )
    new_graduation = date(2028, 6, 1)

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(
        side_effect=[
            MagicMock(scalar_one_or_none=MagicMock(return_value=profile)),
        ]
    )
    mock_db.refresh = AsyncMock()

    monkeypatch.setattr(
        "apps.profiles.services.profile_service.calculate_completeness_score",
        AsyncMock(return_value=75),
    )
    monkeypatch.setattr(
        "apps.recommendations.services.post_keyword_service.refresh_profile_extracted_keywords_best_effort",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "apps.profiles.services.profile_service.get_my_profile_service",
        AsyncMock(return_value={"user": {"graduationDate": format_graduation_date(new_graduation)}}),
    )

    payload = UpdateProfileRequest(graduationDate=new_graduation)
    await update_my_profile_service(user, payload, mock_db)

    assert profile.graduation_date == new_graduation
    assert profile.is_alumni is False
    assert user.has_changed_email_after_graduation is False
    mock_db.add.assert_any_call(user)


@pytest.mark.asyncio
async def test_update_my_profile_service_omitting_graduation_date_is_unchanged(monkeypatch) -> None:
    from apps.profiles.schemas import UpdateProfileRequest
    from apps.profiles.services.profile_service import update_my_profile_service

    user = MagicMock()
    user.id = uuid4()
    user.email = "update-profile@example.com"

    existing_graduation = date(2027, 1, 15)
    profile = Profile(
        user_id=user.id,
        first_name="Test",
        last_name="User",
        graduation_date=existing_graduation,
        is_alumni=True,
        completeness_score=0,
    )

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(
        side_effect=[
            MagicMock(scalar_one_or_none=MagicMock(return_value=profile)),
        ]
    )
    mock_db.refresh = AsyncMock()

    monkeypatch.setattr(
        "apps.profiles.services.profile_service.calculate_completeness_score",
        AsyncMock(return_value=75),
    )
    monkeypatch.setattr(
        "apps.recommendations.services.post_keyword_service.refresh_profile_extracted_keywords_best_effort",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "apps.profiles.services.profile_service.get_my_profile_service",
        AsyncMock(return_value={"user": {"graduationDate": format_graduation_date(existing_graduation)}}),
    )

    payload = UpdateProfileRequest(bio="Only bio updated")
    await update_my_profile_service(user, payload, mock_db)

    assert profile.graduation_date == existing_graduation
    assert profile.is_alumni is True
    mock_db.commit.assert_awaited_once()


def test_is_graduation_completed_tomorrow_is_false() -> None:
    today = date(2026, 9, 2)
    tomorrow = today + timedelta(days=1)
    assert is_graduation_completed(tomorrow, reference_date=today) is False


def test_is_graduation_completed_yesterday_is_true() -> None:
    today = date(2026, 9, 2)
    yesterday = today - timedelta(days=1)
    assert is_graduation_completed(yesterday, reference_date=today) is True


def test_user_model_has_changed_email_after_graduation_default_false() -> None:
    user = User(
        id=uuid4(),
        email="default@example.com",
        firebase_uid="firebase-uid",
        status=UserStatus.active,
    )
    assert user.has_changed_email_after_graduation is False


def test_profile_model_has_is_alumni_field() -> None:
    profile_columns = Profile.model_fields.keys()
    assert "is_alumni" in profile_columns


def test_apply_profile_graduation_date_clears_alumni_for_future_date() -> None:
    profile = Profile(
        user_id=uuid4(),
        first_name="Alum",
        last_name="User",
        graduation_date=date(2020, 1, 1),
        is_alumni=True,
    )
    user = User(
        id=profile.user_id,
        email="alum@example.com",
        firebase_uid="firebase-uid",
        status=UserStatus.active,
        has_changed_email_after_graduation=True,
    )

    apply_profile_graduation_date(profile, date(2026, 12, 10), user=user)

    assert profile.graduation_date == date(2026, 12, 10)
    assert profile.is_alumni is False
    assert user.has_changed_email_after_graduation is False


def test_apply_profile_graduation_date_sets_alumni_for_past_date() -> None:
    profile = Profile(
        user_id=uuid4(),
        first_name="Alum",
        last_name="User",
        graduation_date=date(2026, 9, 10),
        is_alumni=False,
    )
    user = User(
        id=profile.user_id,
        email="alum@example.com",
        firebase_uid="firebase-uid",
        status=UserStatus.active,
        has_changed_email_after_graduation=True,
    )

    apply_profile_graduation_date(profile, date(2026, 8, 1), user=user)

    assert profile.graduation_date == date(2026, 8, 1)
    assert profile.is_alumni is True
    assert user.has_changed_email_after_graduation is True


def test_apply_profile_graduation_date_sets_alumni_null_when_date_missing() -> None:
    profile = Profile(
        user_id=uuid4(),
        first_name="Alum",
        last_name="User",
        graduation_date=date(2020, 1, 1),
        is_alumni=True,
    )
    user = User(
        id=profile.user_id,
        email="alum@example.com",
        firebase_uid="firebase-uid",
        status=UserStatus.active,
        has_changed_email_after_graduation=True,
    )

    apply_profile_graduation_date(profile, None, user=user)

    assert profile.graduation_date is None
    assert profile.is_alumni is None
    assert user.has_changed_email_after_graduation is False


def test_apply_profile_graduation_date_without_user_still_updates_alumni() -> None:
    profile = Profile(
        user_id=uuid4(),
        first_name="Alum",
        last_name="User",
        graduation_date=date(2020, 1, 1),
        is_alumni=True,
    )

    apply_profile_graduation_date(profile, date(2026, 12, 10))

    assert profile.graduation_date == date(2026, 12, 10)
    assert profile.is_alumni is False


def test_profile_model_has_graduation_completion_email_sent_at_field() -> None:
    profile_columns = Profile.model_fields.keys()
    assert "graduation_completion_email_sent_at" in profile_columns
    assert "is_graduation_completed" not in profile_columns
    assert "is_alumni" in profile_columns


def test_user_model_has_changed_email_field_and_no_calculated_graduation_flag() -> None:
    user_columns = User.model_fields.keys()
    assert "has_changed_email_after_graduation" in user_columns
    assert "is_graduation_completed" not in user_columns


def test_sqlmodel_table_has_no_graduation_completed_column() -> None:
    table = Profile.__table__
    column_names = {column.name for column in table.columns}
    assert "is_graduation_completed" not in column_names
    assert "is_alumni" in column_names
    assert "graduation_date" in column_names


def test_build_graduation_completion_email_html_renders_dynamic_content() -> None:
    html = build_graduation_completion_email_html(
        first_name="Alex",
        university_name="Stanford University",
    )

    assert "Congratulations on your graduation! 🎓" in html
    # assert (
    #     "If you signed up with an institutional email, please update it "
    #     "to stay connected on KampuLynk."
    # ) in html
    assert "text-align:center" in html
    assert "Stanford University" in html
    assert "You did it! Your graduation from the Stanford University marks an incredible milestone" in html
    assert "As you begin your next chapter, keep learning, connecting, and sharing your ideas with the world." in html
    assert "From your friends at KampuLynk 😊" in html
    assert "automated security notification" not in html
    assert 'font-size:20px;font-weight:700;color:#FFFFFF;line-height:1.3;text-align:center;">From your friends at KampuLynk 😊' in html
    assert "All rights reserved." in html


def test_build_graduation_completion_email_html_falls_back_for_missing_university() -> None:
    html = build_graduation_completion_email_html(first_name="Jamie", university_name=None)

    assert "Congratulations on your graduation! 🎓" in html
    assert "your university" in html
