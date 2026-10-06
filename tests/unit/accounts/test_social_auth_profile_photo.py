from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from apps.accounts.schemas import SocialAuthRequest
from apps.profiles.schemas import UpdateProfileRequest
from apps.accounts.services import registration_service as reg_svc
from common.enums import UserStatus
from common.exceptions import ApiError


PROVIDER_URL = "https://lh3.googleusercontent.com/a/old-google-photo"
STORED_KEY = "profiles/abc.jpg"
COPIED_KEY = "profiles/copied-uuid.jpg"


def _firebase_user(*, picture: str | None = PROVIDER_URL) -> dict:
    data = {
        "uid": "firebase-uid",
        "email": "social@example.com",
        "name": "Social User",
        "firebase": {"sign_in_provider": "google.com"},
    }
    if picture is not None:
        data["picture"] = picture
    return data


def _payload(*, profile_photo_url: str | None = PROVIDER_URL) -> SocialAuthRequest:
    return SocialAuthRequest(
        loginType="google",
        firebaseId="google-token",
        profile_photo_url=profile_photo_url,
    )


def _existing_user():
    return SimpleNamespace(
        id=uuid.uuid4(),
        email="social@example.com",
        firebase_uid="firebase-uid",
        status=UserStatus.active,
        deleted_at=None,
        is_deleted=False,
        last_login_at=None,
        updated_at=datetime.now(timezone.utc),
        roles=[],
    )


@pytest.mark.asyncio
async def test_store_photo_new_user_copies_to_s3_key_not_provider_url():
    payload = _payload()
    with patch(
        "core.images.copy_remote_image_to_s3",
        AsyncMock(return_value=COPIED_KEY),
    ) as copy_remote:
        stored = await reg_svc._store_social_profile_photo(
            firebase_user=_firebase_user(),
            payload=payload,
            existing_photo_url=None,
        )

    assert stored == COPIED_KEY
    assert stored != PROVIDER_URL
    copy_remote.assert_awaited_once()
    assert copy_remote.await_args.args[0] == PROVIDER_URL


@pytest.mark.asyncio
async def test_store_photo_new_user_without_photo_stays_none():
    payload = _payload(profile_photo_url=None)
    with patch(
        "core.images.copy_remote_image_to_s3",
        AsyncMock(return_value=COPIED_KEY),
    ) as copy_remote:
        stored = await reg_svc._store_social_profile_photo(
            firebase_user=_firebase_user(picture=None),
            payload=payload,
            existing_photo_url=None,
        )

    assert stored is None
    copy_remote.assert_not_called()


@pytest.mark.asyncio
async def test_store_photo_keeps_existing_s3_key_and_ignores_provider_url():
    payload = _payload()
    with patch(
        "core.images.copy_remote_image_to_s3",
        AsyncMock(return_value=COPIED_KEY),
    ) as copy_remote:
        stored = await reg_svc._store_social_profile_photo(
            firebase_user=_firebase_user(),
            payload=payload,
            existing_photo_url=STORED_KEY,
        )

    assert stored == STORED_KEY
    copy_remote.assert_not_called()


@pytest.mark.asyncio
async def test_store_photo_migrates_legacy_http_url_to_s3_key():
    payload = _payload()
    with patch(
        "core.images.copy_remote_image_to_s3",
        AsyncMock(return_value=COPIED_KEY),
    ) as copy_remote:
        stored = await reg_svc._store_social_profile_photo(
            firebase_user=_firebase_user(),
            payload=payload,
            existing_photo_url=PROVIDER_URL,
        )

    assert stored == COPIED_KEY
    copy_remote.assert_awaited_once()


@pytest.mark.asyncio
async def test_store_photo_copy_failure_for_new_user_does_not_store_provider_url():
    payload = _payload()
    with patch(
        "core.images.copy_remote_image_to_s3",
        AsyncMock(return_value=""),
    ):
        stored = await reg_svc._store_social_profile_photo(
            firebase_user=_firebase_user(),
            payload=payload,
            existing_photo_url=None,
        )

    assert stored is None
    assert stored != PROVIDER_URL


@pytest.mark.asyncio
async def test_store_photo_copy_failure_keeps_legacy_url_and_does_not_persist_source():
    other_provider_url = "https://lh3.googleusercontent.com/a/different-photo"
    payload = _payload(profile_photo_url=other_provider_url)
    with patch(
        "core.images.copy_remote_image_to_s3",
        AsyncMock(return_value=""),
    ):
        stored = await reg_svc._store_social_profile_photo(
            firebase_user=_firebase_user(picture=other_provider_url),
            payload=payload,
            existing_photo_url=PROVIDER_URL,
        )

    assert stored == PROVIDER_URL
    assert stored != other_provider_url


@pytest.mark.asyncio
async def test_social_auth_existing_s3_key_not_overwritten_by_provider_url(
    mock_db, scalar_result
):
    user = _existing_user()
    profile = SimpleNamespace(
        user_id=user.id,
        profile_photo_url=STORED_KEY,
        completeness_score=10,
    )
    db = mock_db(scalar_result(user), scalar_result(profile))

    with (
        patch("core.auth.services.verify_firebase_token", return_value=_firebase_user()),
        patch(
            "core.images.copy_remote_image_to_s3",
            AsyncMock(return_value=COPIED_KEY),
        ) as copy_remote,
        patch.object(
            reg_svc,
            "_build_device_auth_session",
            AsyncMock(return_value=({"user": {}}, "Login successful")),
        ),
    ):
        await reg_svc.social_auth(_payload(), db)

    assert profile.profile_photo_url == STORED_KEY
    copy_remote.assert_not_called()


@pytest.mark.asyncio
async def test_social_auth_migrates_legacy_http_url_on_existing_user(
    mock_db, scalar_result
):
    user = _existing_user()
    profile = SimpleNamespace(
        user_id=user.id,
        profile_photo_url=PROVIDER_URL,
        completeness_score=10,
    )
    db = mock_db(scalar_result(user), scalar_result(profile))

    with (
        patch("core.auth.services.verify_firebase_token", return_value=_firebase_user()),
        patch(
            "core.images.copy_remote_image_to_s3",
            AsyncMock(return_value=COPIED_KEY),
        ),
        patch(
            "apps.profiles.services.calculate_completeness_score",
            AsyncMock(return_value=20),
        ),
        patch.object(
            reg_svc,
            "_build_device_auth_session",
            AsyncMock(return_value=({"user": {}}, "Login successful")),
        ),
    ):
        await reg_svc.social_auth(_payload(), db)

    assert profile.profile_photo_url == COPIED_KEY


@pytest.mark.asyncio
async def test_social_auth_copy_failure_on_existing_profile_does_not_persist_provider_url(
    mock_db, scalar_result
):
    user = _existing_user()
    profile = SimpleNamespace(
        user_id=user.id,
        profile_photo_url=None,
        completeness_score=0,
    )
    db = mock_db(scalar_result(user), scalar_result(profile))

    with (
        patch("core.auth.services.verify_firebase_token", return_value=_firebase_user()),
        patch(
            "core.images.copy_remote_image_to_s3",
            AsyncMock(return_value=""),
        ),
        patch.object(
            reg_svc,
            "_build_device_auth_session",
            AsyncMock(return_value=({"user": {}}, "Login successful")),
        ),
    ):
        await reg_svc.social_auth(_payload(), db)

    assert profile.profile_photo_url is None


@pytest.mark.asyncio
async def test_profile_update_key_remains_source_of_truth_on_social_login(
    mock_db, scalar_result
):
    from apps.profiles.services.profile_service import update_my_profile_service

    user = _existing_user()
    profile = SimpleNamespace(
        user_id=user.id,
        first_name="Social",
        last_name="User",
        major=None,
        minor=None,
        bio=None,
        university_id=None,
        country_id=None,
        edu_level=None,
        profile_interests_id=None,
        profile_photo_url=None,
        banner_photo_url=None,
        completeness_score=0,
    )
    updated_key = "profiles/user-uploaded.jpg"
    db = mock_db(scalar_result(profile))

    with (
        patch(
            "core.images.file_exists",
            lambda *_args, **_kwargs: True,
        ),
        patch(
            "apps.notifications.services.topic_service.TopicService.affects_topics",
            lambda _payload: False,
        ),
        patch(
            "apps.profiles.services.profile_service.calculate_completeness_score",
            AsyncMock(return_value=40),
        ),
        patch(
            "apps.recommendations.services.post_keyword_service.refresh_profile_extracted_keywords_best_effort",
            AsyncMock(),
        ),
        patch(
            "apps.chat.service.sync_stream_user_on_auth",
            AsyncMock(),
        ),
        patch(
            "apps.profiles.services.profile_service.get_my_profile_service",
            AsyncMock(return_value={"user": {"profilePhoto_url": updated_key}}),
        ),
    ):
        payload = UpdateProfileRequest.model_validate({"profile_photo_key": updated_key})
        await update_my_profile_service(user, payload, db)

    assert profile.profile_photo_url == updated_key

    with patch(
        "core.images.copy_remote_image_to_s3",
        AsyncMock(return_value=COPIED_KEY),
    ) as copy_remote:
        stored = await reg_svc._store_social_profile_photo(
            firebase_user=_firebase_user(),
            payload=_payload(),
            existing_photo_url=profile.profile_photo_url,
        )

    assert stored == updated_key
    copy_remote.assert_not_called()


@pytest.mark.asyncio
async def test_subsequent_social_after_user_update_cannot_overwrite_s3_key(
    mock_db, scalar_result
):
    user = _existing_user()
    profile = SimpleNamespace(
        user_id=user.id,
        profile_photo_url="profiles/user-updated.jpg",
        completeness_score=40,
    )
    db = mock_db(scalar_result(user), scalar_result(profile))

    with (
        patch("core.auth.services.verify_firebase_token", return_value=_firebase_user()),
        patch(
            "core.images.copy_remote_image_to_s3",
            AsyncMock(return_value=COPIED_KEY),
        ) as copy_remote,
        patch.object(
            reg_svc,
            "_build_device_auth_session",
            AsyncMock(return_value=({"user": {}}, "Login successful")),
        ),
    ):
        await reg_svc.social_auth(_payload(), db)

    assert profile.profile_photo_url == "profiles/user-updated.jpg"
    copy_remote.assert_not_called()


@pytest.mark.asyncio
async def test_subsequent_social_after_admin_update_cannot_overwrite_s3_key(
    mock_db, scalar_result
):
    user = _existing_user()
    admin_key = "profiles/admin-selected.jpg"
    profile = SimpleNamespace(
        user_id=user.id,
        profile_photo_url=admin_key,
        completeness_score=40,
    )
    db = mock_db(scalar_result(user), scalar_result(profile))

    with (
        patch("core.auth.services.verify_firebase_token", return_value=_firebase_user()),
        patch(
            "core.images.copy_remote_image_to_s3",
            AsyncMock(return_value=COPIED_KEY),
        ) as copy_remote,
        patch.object(
            reg_svc,
            "_build_device_auth_session",
            AsyncMock(return_value=({"user": {}}, "Login successful")),
        ),
    ):
        await reg_svc.social_auth(_payload(), db)

    assert profile.profile_photo_url == admin_key
    copy_remote.assert_not_called()


@pytest.mark.asyncio
async def test_complete_firebase_registration_stores_s3_key_not_provider_url(
    mock_db, scalar_result
):
    created = {}

    def _capture_add(obj):
        if isinstance(obj, SimpleNamespace) or obj.__class__.__name__ == "Profile":
            created["profile"] = obj

    db = mock_db(scalar_result(None), scalar_result(None), scalar_result(_existing_user()))
    db.add = _capture_add

    firebase_user = _firebase_user()
    firebase_user["uid"] = "new-firebase-uid"
    firebase_user["email"] = "new-social@example.com"

    with (
        patch.object(reg_svc, "assign_user_role", AsyncMock()),
        patch.object(reg_svc, "log_security_event", AsyncMock()),
        patch(
            "apps.profiles.services.calculate_completeness_score",
            AsyncMock(return_value=10),
        ),
        patch(
            "core.images.copy_remote_image_to_s3",
            AsyncMock(return_value=COPIED_KEY),
        ) as copy_remote,
    ):
        await reg_svc.complete_firebase_registration(firebase_user, db)

    profile = created.get("profile")
    assert profile is not None
    assert profile.profile_photo_url == COPIED_KEY
    assert profile.profile_photo_url != PROVIDER_URL
    copy_remote.assert_awaited_once()


@pytest.mark.asyncio
async def test_social_auth_rejects_stale_google_email(mock_db, scalar_result):
    user = _existing_user()
    user.email = "newemail@example.com"
    db = mock_db(scalar_result(user))

    with (
        patch("core.auth.services.verify_firebase_token", return_value=_firebase_user()),
        pytest.raises(ApiError, match="does not match your current email"),
    ):
        await reg_svc.social_auth(_payload(), db)


@pytest.mark.asyncio
async def test_complete_firebase_registration_rejects_stale_token_email(
    mock_db, scalar_result
):
    user = _existing_user()
    user.email = "newemail@example.com"
    db = mock_db(scalar_result(user))

    with pytest.raises(ApiError, match="does not match your current email"):
        await reg_svc.complete_firebase_registration(_firebase_user(), db)
