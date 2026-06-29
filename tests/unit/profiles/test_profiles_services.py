from __future__ import annotations

import pytest
import uuid
import jwt
from io import BytesIO
from datetime import datetime, date, timezone, timedelta
from unittest.mock import MagicMock
from fastapi import UploadFile, HTTPException
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession
from core.database.session import async_session_factory, engine
from core.database.init import init_db

from apps.accounts.db_models import User
from apps.profiles.db_models import Profile, CompletenessWeight
from common.enums import UserStatus, OnboardingStatus, ProfileVisibility
from apps.profiles.schemas import (
    ProfileUpdateRequest,
    ProfileVisibilityRequest,
    ReportUserRequest,
    CompletenessWeightsUpdateRequest,
    UpdateProfileMeRequest,
)
from core.auth.config import settings as auth_settings

from apps.profiles.services import (
    build_user_base_response,
    get_profile_me,
    update_profile_me,
    delete_user_me,
    update_profile,
    complete_onboarding,
    update_visibility,
    get_me,
    get_me_completeness,
    get_public_profile,
    follow_user,
    unfollow_user,
    block_user,
    unblock_user,
    report_user,
    request_lynkup,
    accept_lynkup,
    remove_lynkup,
    update_profile_me_form,
    get_completeness_weights,
    calculate_completeness_score,
    update_completeness_weights,
)

@pytest.mark.asyncio
async def test_profiles_basic_sync_helpers() -> None:
    # update_profile
    payload_update = ProfileUpdateRequest(bio="Test bio")
    res_update = update_profile(payload_update)
    assert res_update["updated"] is True
    assert res_update["profile"]["bio"] == "Test bio"

    # update_visibility
    payload_vis = ProfileVisibilityRequest(profileVisibility="private")
    res_vis = update_visibility(payload_vis)
    assert res_vis["profileVisibility"] == "private"

    # get_me / get_public_profile / follow/unfollow/block/unblock/report/lynkup
    assert get_me("access_test")["user"]["email"] == "test@example.com"
    assert get_public_profile("test@example.com")["publicProfile"] is True
    assert follow_user("123")["followed"] is True
    assert unfollow_user("123")["unfollowed"] is True
    assert block_user("123")["blocked"] is True
    assert unblock_user("123")["unblocked"] is True
    assert report_user("123", ReportUserRequest(reason="Spam"))["report"]["reason"] == "Spam"
    assert request_lynkup("123")["requestSent"] is True
    assert accept_lynkup("123")["accepted"] is True
    assert remove_lynkup("123")["removed"] is True


@pytest.mark.asyncio
async def test_profiles_get_and_update_me(monkeypatch) -> None:
    try:
        await init_db()
        async def mock_upload(*args, **kwargs):
            return "mocked_path/photo.png"
        monkeypatch.setattr("apps.profiles.services.upload_image_to_s3", mock_upload)
        monkeypatch.setattr("core.auth.services.revoke_firebase_tokens", lambda *args: None)

        async with async_session_factory() as session:
            user = User(
                firebase_uid=f"uid-{uuid.uuid4()}",
                email="user_profile_test@example.com",
                role="user",
                status="active",
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)

        # 1. get_profile_me (should auto-create profile)
        async with async_session_factory() as session:
            res_get = await get_profile_me(user, session)
            assert res_get["user"]["email"] == "user_profile_test@example.com"

        # 2. update_profile_me
        async with async_session_factory() as session:
            # We must query the user in the session
            db_user = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
            payload = UpdateProfileMeRequest(
                firstName="New",
                lastName="User",
                major="Biology",
                bio="New Bio Info",
            )
            res_up = await update_profile_me(db_user, payload, session)
            assert res_up["user"]["bio"] == "New Bio Info"
            assert res_up["user"]["firstName"] == "New"
            assert res_up["user"]["lastName"] == "User"
            assert res_up["user"]["major"] == "Biology"

        # 3. delete_user_me
        async with async_session_factory() as session:
            db_user = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
            res_del = await delete_user_me(db_user, session)
            assert res_del["deleted"] is True
            assert res_del["status"] == "deleting"
            assert "user" in res_del
            assert res_del["user"]["is_deleted"] is True
            assert db_user.is_deleted is True

    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_profiles_complete_onboarding(monkeypatch) -> None:
    try:
        await init_db()
        async with async_session_factory() as session:
            user = User(
                firebase_uid=f"uid-{uuid.uuid4()}",
                email="user_edu_test@example.com",
                role="user",
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)

        async with async_session_factory() as session:
            from apps.profiles.db_models.country_db_model import Country
            from apps.profiles.db_models.university_db_model import University
            stmt_country = select(Country).where(Country.iso_code == "US")
            country = (await session.execute(stmt_country)).scalar_one_or_none()
            if not country:
                country = Country(name="United States", iso_code="US")
                session.add(country)
                await session.flush()
            
            univ = University(name="Test University", slug=f"test-univ-{uuid.uuid4()}", country_id=country.id)
            session.add(univ)
            await session.commit()
            univ_id = univ.id

        from core.images import save_image, file_exists
        monkeypatch.setattr("core.images.save_image", lambda *args, **kwargs: None)
        monkeypatch.setattr("core.images.file_exists", lambda *args, **kwargs: True)

        from common.enums import EducationLevel

        async with async_session_factory() as session:
            res = await complete_onboarding(
                user=user,
                bio="Test bio",
                major="Physics",
                minor="Math",
                university_id=str(univ_id),
                education_level_id=2,
                academic_interests=["Math", "Physics"],
                profile_photo_key="profiles/test.png",
                banner_photo_key=None,
                db=session,
            )
            assert res["user"]["id"] == str(user.id)
            assert res["user"]["major"] == "Physics"
            assert res["user"]["educationLevel"] == EducationLevel.masters.value
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_get_me_completeness(monkeypatch) -> None:
    try:
        await init_db()
        async with async_session_factory() as session:
            user = User(
                firebase_uid=f"uid-{uuid.uuid4()}",
                email="user_comp_test@example.com",
                role="user",
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            
            profile = Profile(user_id=user.id, first_name="Test", last_name="", completeness_score=75)
            session.add(profile)
            await session.commit()

        async with async_session_factory() as session:
            res = await get_me_completeness(user.id, session)
            assert res["completeness_score"] == 75
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_profiles_form_update(monkeypatch) -> None:
    try:
        await init_db()
        # Mock save_image
        mock_save = MagicMock()
        monkeypatch.setattr("core.images.save_image", mock_save)
        monkeypatch.setattr("apps.profiles.services.normalize_image_name", lambda x: x)

        async with async_session_factory() as session:
            user = User(
                firebase_uid=f"uid-{uuid.uuid4()}",
                email="user_form_test@example.com",
                role="user",
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)

        # Run form update
        async with async_session_factory() as session:
            db_user = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
            
            p_photo = UploadFile(filename="photo.jpg", file=BytesIO(b"photo-data"))
            b_photo = UploadFile(filename="banner.jpg", file=BytesIO(b"banner-data"))
            
            res = await update_profile_me_form(
                current_user=db_user,
                bio="Form Bio",
                academic_interests="[Math, History]",
                profile_photo=p_photo,
                banner_photo=b_photo,
                db=session
            )
            assert res["user"]["bio"] == "Form Bio"
            assert "Math" in res["user"]["academicInterests"]
            assert mock_save.call_count == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_update_completeness_weights() -> None:
    try:
        await init_db()
        async with async_session_factory() as session:
            # Seed a default weights row
            w = await get_completeness_weights(session)
            assert w.bio > 0

        async with async_session_factory() as session:
            payload = CompletenessWeightsUpdateRequest(
                bio=15.0,
                location=5.0,
            )
            res = await update_completeness_weights(payload, session)
            assert res["weights"]["bio"] == 15.0
            assert res["weights"]["location"] == 5.0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_update_user_profile_by_admin_service(monkeypatch) -> None:
    from apps.profiles.services import update_user_profile_by_admin_service
    from apps.profiles.schemas import UpdateProfileRequest

    # Mock file_exists to return True
    monkeypatch.setattr("core.images.file_exists", lambda key: True)
    # Mock normalize_image_name
    monkeypatch.setattr("core.images.normalize_image_name", lambda key: f"normalized/{key}")

    try:
        await init_db()
        async with async_session_factory() as session:
            # Create user
            db_user = User(
                email="admin_edit_user@example.com",
                role="user",
                firebase_uid="uid_admin_edit_user",
            )
            session.add(db_user)
            await session.commit()
            await session.refresh(db_user)
            user_id = db_user.id

        async with async_session_factory() as session:
            payload = UpdateProfileRequest(
                firstName="Alice",
                lastName="Smith",
                bio="Bio updated by admin",
                profile_photo_key="admin_photo.png",
                banner_photo_key="admin_banner.png",
            )
            res = await update_user_profile_by_admin_service(
                user_id=user_id,
                payload=payload,
                db=session,
            )
            assert res["user"]["firstName"] == "Alice"
            assert res["user"]["lastName"] == "Smith"
            assert res["user"]["bio"] == "Bio updated by admin"
            assert "admin_photo.png" in res["user"]["profilePhoto_url"]
            assert "admin_banner.png" in res["user"]["bannerPhotoUrl"]

    finally:
        await engine.dispose()

