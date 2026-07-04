from __future__ import annotations

import pytest
import uuid
import jwt
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException
from sqlmodel import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from core.database.session import async_session_factory, engine
from core.database.init import init_db

from apps.accounts.db_models import User, Role, UserRole, RefreshToken
from apps.profiles.db_models import Profile
from common.enums import UserStatus, OnboardingStatus, EducationLevel

from apps.accounts.schemas import EmailSignupRequest, RefreshTokenRequest
from apps.administration.schemas import (
    AdminUserActionRequest,
    ChangePasswordRequest,
    AdminUserCreateRequest,
    AdminLoginRequest,
)

from apps.administration.services import (
    admin_token,
    admin_signin,
    list_users,
    export_users,
    admin_create_user,
    admin_get_user,
    admin_delete_user,
    admin_update_user_status,
    change_password,
    PASSWORD_HASHER,
    _generate_admin_tokens,
)
from common.enums import AdminUserStatus
from apps.accounts.services import JWT_SECRET, JWT_ALGORITHM, _generate_tokens



@pytest.mark.asyncio
async def test_admin_token_success(monkeypatch) -> None:
    try:
        await init_db()
        # Create a user in DB
        async with async_session_factory() as session:
            # Seed user
            uid = str(uuid.uuid4())
            user = User(
                firebase_uid=uid,
                email="user_admin_token_test@example.com",
                role="superadmin",
                status="active",
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            
            # Generate actual tokens
            _access, refresh = _generate_admin_tokens(user)
            
        async with async_session_factory() as session:
            payload = RefreshTokenRequest(refreshToken=refresh)
            res = await admin_token(payload, session)
            assert "access_token" in res
            assert res["token_type"] == "bearer"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_admin_token_expired(monkeypatch) -> None:
    payload = RefreshTokenRequest(refreshToken="invalid-token")
    async with async_session_factory() as session:
        with pytest.raises(HTTPException) as exc:
            await admin_token(payload, session)
        assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_list_users() -> None:
    try:
        await init_db()
        async with async_session_factory() as session:
            uid1 = str(uuid.uuid4())
            uid2 = str(uuid.uuid4())
            user1 = User(firebase_uid=uid1, email="user_list1@example.com", role="user")
            user2 = User(firebase_uid=uid2, email="user_list2@example.com", role="user")
            session.add(user1)
            session.add(user2)
            await session.flush()
            from apps.accounts.services import assign_user_role
            await assign_user_role(session, user1, "user")
            await assign_user_role(session, user2, "user")
            await session.commit()
            
        async with async_session_factory() as session:
            res = await list_users(page=1, page_size=10, db=session)
            assert "items" in res
            assert len(res["items"]) >= 2
            emails = [item["email"] for item in res["items"]]
            assert "user_list1@example.com" in emails
            assert "user_list2@example.com" in emails
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_admin_create_user_via_signup_removed_uses_admin_create_user(monkeypatch) -> None:
    """Public admin signup was removed; superadmin creates users via admin_create_user."""
    try:
        await init_db()
        async def _mock_temp_password_email(*_args, **_kwargs):
            return True

        def _mock_create_firebase_user(email, password, display_name=None):
            class MockFirebaseUser:
                uid = "mock-firebase-uid"
            return MockFirebaseUser()

        monkeypatch.setattr("core.email_service.send_temporary_password_email", _mock_temp_password_email)
        monkeypatch.setattr("core.auth.services.create_firebase_user", _mock_create_firebase_user)

        email = f"user_admin_create_{uuid.uuid4()}@example.com"
        payload = AdminUserCreateRequest(
            firstName="John",
            lastName="Doe",
            email=email,
            role="user",
        )
        async with async_session_factory() as session:
            res = await admin_create_user(payload, session)
            assert res.status is True

            stmt = select(User).where(User.email == email)
            user = (await session.execute(stmt)).scalar_one_or_none()
            assert user is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_admin_create_moderator_local_user(monkeypatch) -> None:
    try:
        await init_db()

        async def _mock_temp_password_email(*_args, **_kwargs):
            return True

        monkeypatch.setattr("core.email_service.send_temporary_password_email", _mock_temp_password_email)

        email = f"moderator_admin_create_{uuid.uuid4()}@example.com"
        payload = AdminUserCreateRequest(
            firstName="Mod",
            lastName="User",
            email=email,
            role="moderator",
        )

        async with async_session_factory() as session:
            res = await admin_create_user(payload, session)
            assert res.status is True
            assert res.data["authProvider"] == "local"
            assert res.data["emailSent"] is True

            stmt = select(User).options(selectinload(User.roles)).where(User.email == email)
            user = (await session.execute(stmt)).scalar_one()
            assert user.firebase_uid is None
            assert user.password_hash is not None
            assert user.role == "moderator"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_admin_create_user_creates_firebase_account(monkeypatch) -> None:
    try:
        await init_db()

        firebase_uid = f"firebase-created-user-{uuid.uuid4()}"

        class _FirebaseUser:
            uid = firebase_uid

        def _mock_create_firebase_user(**kwargs):
            assert kwargs["email"].endswith("@example.com")
            assert kwargs["password"]
            return _FirebaseUser()

        async def _mock_temp_password_email(*_args, **_kwargs):
            return True

        monkeypatch.setattr("core.auth.services.create_firebase_user", _mock_create_firebase_user)
        monkeypatch.setattr("core.auth.services.delete_firebase_user", lambda *_args, **_kwargs: None)
        monkeypatch.setattr("core.email_service.send_temporary_password_email", _mock_temp_password_email)

        email = f"firebase_admin_create_{uuid.uuid4()}@example.com"
        payload = AdminUserCreateRequest(
            firstName="Firebase",
            lastName="User",
            email=email,
            role="user",
        )

        async with async_session_factory() as session:
            res = await admin_create_user(payload, session)
            assert res.status is True
            assert res.data["authProvider"] == "firebase"

            stmt = select(User).options(selectinload(User.roles)).where(User.email == email)
            user = (await session.execute(stmt)).scalar_one()
            assert user.firebase_uid == firebase_uid
            assert user.password_hash is not None
            assert user.role == "user"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_admin_change_password_updates_firebase_for_firebase_user(monkeypatch) -> None:
    try:
        await init_db()

        updated_passwords = []

        def _mock_update_firebase_password(uid, password):
            updated_passwords.append((uid, password))

        monkeypatch.setattr("core.auth.services.update_firebase_password", _mock_update_firebase_password)

        firebase_uid = f"firebase-password-user-{uuid.uuid4()}"
        async with async_session_factory() as session:
            user = User(
                firebase_uid=firebase_uid,
                email=f"firebase_password_{uuid.uuid4()}@example.com",
                password_hash=PASSWORD_HASHER.hash("OldPassword123!"),
                status=UserStatus.active,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)

            res = await change_password(
                ChangePasswordRequest(
                    current_password="OldPassword123!",
                    new_password="NewPassword123!",
                ),
                user,
                session,
            )

            assert res.status is True
            assert updated_passwords == [(firebase_uid, "NewPassword123!")]
            assert PASSWORD_HASHER.verify("NewPassword123!", user.password_hash)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_admin_change_password_local_user_does_not_call_firebase(monkeypatch) -> None:
    try:
        await init_db()

        def _raise_if_called(*_args, **_kwargs):
            raise AssertionError("Firebase should not be called for local users")

        monkeypatch.setattr("core.auth.services.update_firebase_password", _raise_if_called)

        async with async_session_factory() as session:
            user = User(
                email=f"local_password_{uuid.uuid4()}@example.com",
                password_hash=PASSWORD_HASHER.hash("OldPassword123!"),
                status=UserStatus.active,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)

            res = await change_password(
                ChangePasswordRequest(
                    current_password="OldPassword123!",
                    new_password="NewPassword123!",
                ),
                user,
                session,
            )

            assert res.status is True
            assert PASSWORD_HASHER.verify("NewPassword123!", user.password_hash)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_admin_get_user_not_found() -> None:
    try:
        await init_db()
        async with async_session_factory() as session:
            with pytest.raises(HTTPException) as exc:
                await admin_get_user(str(uuid.uuid4()), session)
            assert exc.value.status_code == 404
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_export_users() -> None:
    try:
        await init_db()
        async with async_session_factory() as session:
            # Seed country, university and academic interests
            from apps.profiles.db_models.country_db_model import Country
            from apps.profiles.db_models.university_db_model import University
            from apps.profiles.db_models.academic_interests_db_model import AcademicInterest

            stmt_country = select(Country).where(Country.iso_code == "US")
            country = (await session.execute(stmt_country)).scalar_one_or_none()
            if not country:
                country = Country(name="United States", iso_code="US")
                session.add(country)
                await session.flush()

            univ = University(name="Stanford University", slug=f"stanford-{uuid.uuid4()}", country_id=country.id)
            session.add(univ)
            await session.flush()

            stmt_interest = select(AcademicInterest).where(AcademicInterest.name == "Artificial Intelligence")
            interest = (await session.execute(stmt_interest)).scalar_one_or_none()
            if not interest:
                interest = AcademicInterest(name="Artificial Intelligence", is_active=True)
                session.add(interest)
                await session.flush()

            # Seed user 1
            uid1 = str(uuid.uuid4())
            email1 = f"export_user1_{uid1}@example.com"
            user1 = User(firebase_uid=uid1, email=email1, role="user")
            session.add(user1)
            await session.flush()

            profile1 = Profile(
                user_id=user1.id,
                first_name="Export",
                last_name="One",
                completeness_score=10,
                university_id=univ.id,
                country_id=country.id,
                profile_interests_id=[str(interest.id)],
            )
            session.add(profile1)

            # Seed user 2
            uid2 = str(uuid.uuid4())
            email2 = f"export_user2_{uid2}@example.com"
            user2 = User(firebase_uid=uid2, email=email2, role="user")
            session.add(user2)
            await session.flush()

            profile2 = Profile(
                user_id=user2.id,
                first_name="Export",
                last_name="Two",
                completeness_score=20,
            )
            session.add(profile2)

            # Assign roles
            stmt_role = select(Role).where(Role.name == "user")
            role_rec = (await session.execute(stmt_role)).scalar_one_or_none()
            if not role_rec:
                role_rec = Role(name="user")
                session.add(role_rec)
                await session.flush()

            ur1 = UserRole(user_id=user1.id, role_id=role_rec.id)
            ur2 = UserRole(user_id=user2.id, role_id=role_rec.id)
            session.add(ur1)
            session.add(ur2)

            await session.commit()

        async with async_session_factory() as session:
            # Test default case: list all users
            res_all = await export_users(None, None, session)
            assert res_all["totalItems"] >= 2
            assert res_all["totalPages"] == 1
            assert len(res_all["items"]) >= 2
            assert res_all["totalItems"] == len(res_all["items"])

            # Verify names are displayed instead of IDs
            item1 = next(item for item in res_all["items"] if item["email"] == email1)
            assert item1["university"] == "Stanford University"
            assert item1["country"] == "United States"
            assert item1["county"] == "United States"
            assert "Artificial Intelligence" in item1["academicInterests"]

            item2 = next(item for item in res_all["items"] if item["email"] == email2)
            assert item2["university"] is None
            assert item2["country"] is None
            assert item2["county"] == ""
            assert len(item2["academicInterests"]) == 0

            # Test paginated case
            res_paginated = await export_users(1, 1, session)
            assert res_paginated["totalItems"] >= 2
            assert res_paginated["totalPages"] >= 2
            assert len(res_paginated["items"]) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_admin_actions(monkeypatch) -> None:
    try:
        await init_db()
        monkeypatch.setattr("core.auth.services.revoke_firebase_tokens", lambda *args: None)
        monkeypatch.setattr("core.auth.services.disable_firebase_user", lambda *args: None)
        monkeypatch.setattr("core.auth.services.enable_firebase_user", lambda *args: None)
        
        async with async_session_factory() as session:
            uid = str(uuid.uuid4())
            email = f"user_actions_{uid}@example.com"
            user = User(
                firebase_uid=uid,
                email=email,
                role="user",
                status="active",
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            
        # Test delete user
        async with async_session_factory() as session:
            del_res = await admin_delete_user(str(user.id), session)
            assert del_res["deleted"] is True
            assert del_res["status"] == "deleting"
            assert "user" in del_res
            assert del_res["user"]["is_deleted"] is True
            db_user = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
            assert db_user.is_deleted is True
            
        # Test suspend user
        async with async_session_factory() as session:
            sus_res = await admin_update_user_status(str(user.id), AdminUserStatus.suspended, session)
            assert sus_res["status"] == "suspended"
            
        # Test ban user
        async with async_session_factory() as session:
            ban_res = await admin_update_user_status(str(user.id), AdminUserStatus.banned, session)
            assert ban_res["status"] == "banned"
            
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_admin_signin_success() -> None:
    try:
        await init_db()
        email = f"admin_success_{uuid.uuid4()}@example.com"
        async with async_session_factory() as session:
            # Create a local admin
            user = User(
                email=email,
                password_hash=PASSWORD_HASHER.hash("AdminPassword123!"),
                status=UserStatus.active,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            
            from apps.accounts.services import assign_user_role
            await assign_user_role(session, user, "superadmin")
            await session.commit()
            
            profile = Profile(user_id=user.id, first_name="Admin", last_name="User")
            session.add(profile)
            await session.commit()

        async with async_session_factory() as session:
            payload = AdminLoginRequest(email=email, password="AdminPassword123!")
            res = await admin_signin(payload, session)
            assert res.status is True
            assert "accessToken" in res.data
            assert "refreshToken" in res.data
            assert res.data["user"]["email"] == email
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_admin_signin_invalid_credentials() -> None:
    try:
        await init_db()
        email = f"admin_fail_{uuid.uuid4()}@example.com"
        async with async_session_factory() as session:
            user = User(
                email=email,
                password_hash=PASSWORD_HASHER.hash("AdminPassword123!"),
                status=UserStatus.active,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            
            from apps.accounts.services import assign_user_role
            await assign_user_role(session, user, "superadmin")
            await session.commit()

        async with async_session_factory() as session:
            payload = AdminLoginRequest(email=email, password="WrongPassword123!")
            res = await admin_signin(payload, session)
            assert res.status is False
            assert "Invalid credentials" in res.message
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_admin_signin_non_superadmin() -> None:
    try:
        await init_db()
        email = f"admin_user_{uuid.uuid4()}@example.com"
        async with async_session_factory() as session:
            user = User(
                email=email,
                password_hash=PASSWORD_HASHER.hash("AdminPassword123!"),
                status=UserStatus.active,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            
            from apps.accounts.services import assign_user_role
            await assign_user_role(session, user, "user")
            await session.commit()

        async with async_session_factory() as session:
            payload = AdminLoginRequest(email=email, password="AdminPassword123!")
            res = await admin_signin(payload, session)
            assert res.status is False
            assert "Forbidden" in res.message
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_admin_token_expiration_rules() -> None:
    try:
        await init_db()
        email = f"admin_token_{uuid.uuid4()}@example.com"
        async with async_session_factory() as session:
            user = User(
                email=email,
                status=UserStatus.active,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            
            from apps.accounts.services import assign_user_role
            await assign_user_role(session, user, "superadmin")
            await session.commit()
            
            # Fetch user with roles loaded eagerly
            stmt = select(User).options(selectinload(User.roles)).where(User.id == user.id)
            user = (await session.execute(stmt)).scalar_one()

        # Generate tokens
        access_token, refresh_token = _generate_admin_tokens(user)
        
        # Decode access token and verify exp is in ~1 day
        decoded_access = jwt.decode(access_token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        exp_time = datetime.fromtimestamp(decoded_access["exp"], tz=timezone.utc)
        now = datetime.now(timezone.utc)
        diff = exp_time - now
        assert abs(diff.total_seconds() - 86400) < 60  # close to 24 hours (1 day)

        # Decode refresh token and verify exp is NOT in refresh payload
        decoded_refresh = jwt.decode(refresh_token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        assert "exp" not in decoded_refresh
    finally:
        await engine.dispose()
