from __future__ import annotations

import pytest
import uuid
import jwt
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, call, patch
from fastapi import HTTPException
from sqlmodel import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from core.database.session import async_session_factory, engine
from core.database.init import init_db

from apps.accounts.db_models import User, Role, UserRole, RefreshToken, ConsentRecord, PasswordResetToken
from apps.profiles.db_models import Profile
from common.enums import UserStatus, OnboardingStatus, EducationLevel

from apps.accounts.schemas import EmailSignupRequest, RefreshTokenRequest
from apps.administration.schemas import (
    AdminUserActionRequest,
    AdminResetPasswordRequest,
    ChangePasswordRequest,
    AdminUserCreateRequest,
    AdminLoginRequest,
    AdminSignupRequest,
)

from apps.administration.services import (
    admin_token,
    admin_signin,
    admin_signup,
    list_users,
    export_users,
    admin_create_user,
    admin_get_user,
    admin_delete_user,
    admin_update_user_status,
    admin_reset_password,
    change_password,
    PASSWORD_HASHER,
    _generate_admin_tokens,
)
from common.enums import AdminUserStatus, inactive_account_message
from common.exceptions import ApiError
from apps.accounts.services import ACCESS_TOKEN_EXPIRE_MINUTES, JWT_SECRET, JWT_ALGORITHM, _generate_tokens



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
                password_hash=PASSWORD_HASHER.hash("AdminPassword123!"),
                role="superadmin",
                status="active",
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            
            from apps.administration.services.session_service import create_admin_session

            admin_session = await create_admin_session(session, user)
            await session.commit()
            # Generate actual tokens bound to independent session_id
            _access, refresh = _generate_admin_tokens(user, session_id=admin_session.id)
            
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
async def test_admin_lists_include_deleting_status_users(monkeypatch) -> None:
    from apps.administration.services import user_management_service as ums
    from common.enums import UserStatus

    deleting_user = {
        "email": "deleting_user@example.com",
        "status": UserStatus.deleting.value,
        "is_deleted": True,
    }
    deleting_mod = {
        "email": "deleting_mod@example.com",
        "status": UserStatus.deleting.value,
        "is_deleted": True,
    }
    deleting_viewer = {
        "email": "deleting_viewer@example.com",
        "status": UserStatus.deleting.value,
        "is_deleted": True,
    }

    async def _fake_fetch(_db, page=None, page_size=None, search=None, role=None, *args, **kwargs):
        if role == "user":
            return [deleting_user]
        if role == "moderator":
            return [deleting_mod]
        if role == "viewer":
            return [deleting_viewer]
        return []


    class _Scalar:
        def scalar_one(self):
            return 1

    class _Session:
        async def execute(self, stmt):
            compiled = str(stmt)
            assert "deleting" in compiled.lower() or "is_deleted" in compiled.lower()
            return _Scalar()

    monkeypatch.setattr(ums, "_fetch_users_with_details", _fake_fetch)

    db = _Session()
    users = await ums.list_users(page=1, page_size=10, db=db)
    moderators = await ums.list_moderators(page=1, page_size=10, db=db)
    viewers = await ums.list_viewer(page=1, page_size=10, db=db)

    assert users["items"][0]["email"] == "deleting_user@example.com"
    assert users["items"][0]["status"] == "deleting"
    assert moderators["items"][0]["email"] == "deleting_mod@example.com"
    assert viewers["items"][0]["email"] == "deleting_viewer@example.com"

    clause = str(ums._admin_visible_users_clause().compile(compile_kwargs={"literal_binds": True}))
    assert "deleting" in clause
    assert "is_deleted" in clause


@pytest.mark.asyncio
async def test_admin_create_user_via_signup_removed_uses_admin_create_user(monkeypatch) -> None:
    """Public admin signup was removed; superadmin creates users via admin_create_user."""
    try:
        await init_db()
        async def _mock_temp_password_email(*_args, **_kwargs):
            return True

        def _mock_create_firebase_user(*, email, password, display_name=None):
            class MockFirebaseUser:
                uid = f"mock-firebase-uid-{email}"
            return MockFirebaseUser()

        monkeypatch.setattr("core.email_service.send_temporary_password_email", _mock_temp_password_email)
        monkeypatch.setattr(
            "apps.administration.services.user_management_service.create_firebase_user",
            _mock_create_firebase_user,
        )
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

        monkeypatch.setattr(
            "apps.administration.services.user_management_service.create_firebase_user",
            _mock_create_firebase_user,
        )
        monkeypatch.setattr(
            "apps.administration.services.user_management_service.delete_firebase_user",
            lambda *_args, **_kwargs: None,
        )
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

            consent_stmt = select(ConsentRecord).where(ConsentRecord.user_id == user.id)
            consents = (await session.execute(consent_stmt)).scalars().all()
            assert len(consents) == 1
            assert consents[0].consent_type == "terms_and_conditions"
            assert consents[0].granted is True
            assert consents[0].ip_address is None
            assert consents[0].consented_at is not None
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
async def test_admin_reset_password_allows_regular_app_user(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    token_val = str(uuid.uuid4())
    user_id = uuid.uuid4()

    user = User(
        id=user_id,
        firebase_uid="firebase-app-user",
        email="app_user_reset@example.com",
        password_hash=PASSWORD_HASHER.hash("OldPassword123!"),
        status=UserStatus.active,
    )
    user.roles = []

    reset_token = PasswordResetToken(
        user_id=user_id,
        token=token_val,
        expires_at=now + timedelta(minutes=30),
    )

    execute_results = iter(
        [
            MagicMock(scalar_one_or_none=MagicMock(return_value=reset_token)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=user)),
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
        ]
    )
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=lambda *_args, **_kwargs: next(execute_results))

    firebase_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "core.auth.services.update_firebase_password",
        lambda uid, password: firebase_calls.append((uid, password)),
    )
    monkeypatch.setattr("core.auth.services.revoke_firebase_tokens", lambda _uid: None)

    res = await admin_reset_password(
        AdminResetPasswordRequest(token=token_val, new_password="NewPassword123!"),
        mock_db,
    )

    assert res.status is True
    assert user.role == "user"
    assert PASSWORD_HASHER.verify("NewPassword123!", user.password_hash)
    assert firebase_calls == [("firebase-app-user", "NewPassword123!")]


@pytest.mark.asyncio
async def test_admin_reset_password_succeeds_when_firebase_user_missing(monkeypatch) -> None:
    from firebase_admin import auth

    now = datetime.now(timezone.utc)
    token_val = str(uuid.uuid4())
    stale_uid = "stale-firebase-uid"
    user_id = uuid.uuid4()

    user = User(
        id=user_id,
        firebase_uid=stale_uid,
        email="moderator_reset@example.com",
        password_hash=PASSWORD_HASHER.hash("OldPassword123!"),
        status=UserStatus.active,
    )
    moderator_role = Role(id=uuid.uuid4(), name="moderator")
    user.roles = [UserRole(user_id=user_id, role_id=moderator_role.id, role=moderator_role)]

    reset_token = PasswordResetToken(
        user_id=user_id,
        token=token_val,
        expires_at=now + timedelta(minutes=30),
    )

    execute_results = iter(
        [
            MagicMock(scalar_one_or_none=MagicMock(return_value=reset_token)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=user)),
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
        ]
    )
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=lambda *_args, **_kwargs: next(execute_results))

    def _raise_user_not_found(_uid, _password):
        raise auth.UserNotFoundError(
            "No user record found for the given identifier (USER_NOT_FOUND)."
        )

    monkeypatch.setattr("core.auth.services.update_firebase_password", _raise_user_not_found)
    revoked_uids: list[str] = []
    monkeypatch.setattr(
        "core.auth.services.revoke_firebase_tokens",
        lambda uid: revoked_uids.append(uid),
    )

    res = await admin_reset_password(
        AdminResetPasswordRequest(token=token_val, new_password="NewPassword123!"),
        mock_db,
    )

    assert res.status is True
    assert res.message == "Password reset successful"
    assert PASSWORD_HASHER.verify("NewPassword123!", user.password_hash)
    assert user.firebase_uid is None
    assert revoked_uids == []
    assert reset_token.used_at is not None
    mock_db.commit.assert_awaited_once()


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
async def test_admin_change_password_success_with_different_new_password(monkeypatch) -> None:
    original_hash = PASSWORD_HASHER.hash("OldPassword123!")
    user = User(
        firebase_uid="firebase-uid-admin-success",
        email="admin_success_pw@example.com",
        password_hash=original_hash,
        status=UserStatus.active,
    )
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(
        return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))))
    )

    firebase_calls: list[tuple[str, str]] = []
    revoked_uids: list[str] = []
    monkeypatch.setattr(
        "core.auth.services.update_firebase_password",
        lambda uid, password: firebase_calls.append((uid, password)),
    )
    monkeypatch.setattr(
        "core.auth.services.revoke_firebase_tokens",
        lambda uid: revoked_uids.append(uid),
    )

    res = await change_password(
        ChangePasswordRequest(
            current_password="OldPassword123!",
            new_password="NewPassword123!",
        ),
        user,
        mock_db,
    )

    assert res.status is True
    assert res.message == "Password updated successfully. Please sign in again."
    assert res.data is None
    assert user.password_hash != original_hash
    assert PASSWORD_HASHER.verify("NewPassword123!", user.password_hash)
    assert firebase_calls == [("firebase-uid-admin-success", "NewPassword123!")]
    assert revoked_uids == ["firebase-uid-admin-success"]
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_change_password_rejects_incorrect_current_password(monkeypatch) -> None:
    original_hash = PASSWORD_HASHER.hash("OldPassword123!")
    user = User(
        firebase_uid="firebase-uid-admin-wrong",
        email="admin_wrong_pw@example.com",
        password_hash=original_hash,
        status=UserStatus.active,
    )
    mock_db = AsyncMock()

    firebase_calls: list[tuple[str, str]] = []
    revoked_uids: list[str] = []
    monkeypatch.setattr(
        "core.auth.services.update_firebase_password",
        lambda uid, password: firebase_calls.append((uid, password)),
    )
    monkeypatch.setattr(
        "core.auth.services.revoke_firebase_tokens",
        lambda uid: revoked_uids.append(uid),
    )

    res = await change_password(
        ChangePasswordRequest(
            current_password="WrongPassword123!",
            new_password="NewPassword123!",
        ),
        user,
        mock_db,
    )

    assert res.status is False
    assert res.message == "existing password does not match"
    assert res.data is None
    assert user.password_hash == original_hash
    assert firebase_calls == []
    assert revoked_uids == []
    mock_db.execute.assert_not_awaited()
    mock_db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_change_password_rejects_same_new_password(monkeypatch) -> None:
    original_hash = PASSWORD_HASHER.hash("Password123")
    user = User(
        firebase_uid="firebase-uid-admin-same",
        email="admin_same_pw@example.com",
        password_hash=original_hash,
        status=UserStatus.active,
    )
    mock_db = AsyncMock()

    firebase_calls: list[tuple[str, str]] = []
    revoked_uids: list[str] = []
    monkeypatch.setattr(
        "core.auth.services.update_firebase_password",
        lambda uid, password: firebase_calls.append((uid, password)),
    )
    monkeypatch.setattr(
        "core.auth.services.revoke_firebase_tokens",
        lambda uid: revoked_uids.append(uid),
    )

    res = await change_password(
        ChangePasswordRequest(
            current_password="Password123",
            new_password="Password123",
        ),
        user,
        mock_db,
    )

    assert res.status is False
    assert res.message == "New password cannot be the same as current password"
    assert res.data is None
    assert user.password_hash == original_hash
    assert firebase_calls == []
    assert revoked_uids == []
    mock_db.execute.assert_not_awaited()
    mock_db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_change_password_invalidates_all_sessions() -> None:
    from apps.administration.services.auth_service import validate_admin_session_token

    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        email="admin_session_invalidate@example.com",
        password_hash=PASSWORD_HASHER.hash("OldPassword123!"),
        status=UserStatus.active,
    )
    user.role = "moderator"
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(
        return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))))
    )

    old_access, old_refresh = _generate_admin_tokens(user, session_id=uuid.uuid4())
    old_access_decoded = jwt.decode(old_access, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    old_refresh_decoded = jwt.decode(old_refresh, JWT_SECRET, algorithms=[JWT_ALGORITHM])

    res = await change_password(
        ChangePasswordRequest(
            current_password="OldPassword123!",
            new_password="NewPassword123!",
        ),
        user,
        mock_db,
    )

    assert res.status is True
    assert res.data is None

    with pytest.raises(ApiError, match="Session expired"):
        validate_admin_session_token(old_access_decoded, user)

    with pytest.raises(ApiError, match="Session expired"):
        validate_admin_session_token(old_refresh_decoded, user)


@pytest.mark.asyncio
async def test_admin_token_rejects_refresh_after_password_change(monkeypatch) -> None:
    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        email="admin_refresh_invalidate@example.com",
        password_hash=PASSWORD_HASHER.hash("Password123!"),
        status=UserStatus.active,
    )
    user.role = "superadmin"
    user.roles = []

    _access, refresh = _generate_admin_tokens(user, session_id=uuid.uuid4())
    user.password_hash = PASSWORD_HASHER.hash("NewPassword123!")

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(
        return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=user),
        )
    )

    with pytest.raises(HTTPException) as exc:
        await admin_token(RefreshTokenRequest(refreshToken=refresh), mock_db)

    assert exc.value.status_code == 401
    assert "Session expired" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_admin_reset_password_invalidates_existing_admin_sessions(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    token_val = str(uuid.uuid4())
    user_id = uuid.uuid4()
    original_hash = PASSWORD_HASHER.hash("OldPassword123!")

    user = User(
        id=user_id,
        email="staff_reset@example.com",
        password_hash=original_hash,
        status=UserStatus.active,
    )
    user.roles = []
    _old_access, _old_refresh = _generate_admin_tokens(user, session_id=uuid.uuid4())

    reset_token = PasswordResetToken(
        user_id=user_id,
        token=token_val,
        expires_at=now + timedelta(minutes=30),
    )

    execute_results = iter(
        [
            MagicMock(scalar_one_or_none=MagicMock(return_value=reset_token)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=user)),
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
        ]
    )
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=lambda *_args, **_kwargs: next(execute_results))
    monkeypatch.setattr("core.auth.services.revoke_firebase_tokens", lambda _uid: None)

    res = await admin_reset_password(
        AdminResetPasswordRequest(token=token_val, new_password="NewPassword123!"),
        mock_db,
    )

    assert res.status is True
    assert user.password_hash != original_hash
    new_access, _new_refresh = _generate_admin_tokens(user, session_id=uuid.uuid4())
    assert new_access != _old_access


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
@pytest.mark.asyncio
async def test_list_users_includes_moderation_notes(monkeypatch) -> None:
    from apps.administration.services import user_management_service as ums
    from common.enums import ReportEntityType

    user_id = uuid.uuid4()
    other_id = uuid.uuid4()

    async def _fake_fetch(_db, page=None, page_size=None, search=None, role=None, *args, **kwargs):
        return [
            {"id": str(user_id), "email": "with_notes@example.com", "moderation_notes": "Spam / harassment"},
            {"id": str(other_id), "email": "no_notes@example.com", "moderation_notes": None},
        ]


    class _Scalar:
        def scalar_one(self):
            return 2

    class _Session:
        async def execute(self, stmt):
            return _Scalar()

    monkeypatch.setattr(ums, "_fetch_users_with_details", _fake_fetch)

    res = await ums.list_users(page=1, page_size=10, db=_Session())
    assert res["items"][0]["moderation_notes"] == "Spam / harassment"
    assert res["items"][1]["moderation_notes"] is None


@pytest.mark.asyncio
async def test_fetch_users_with_details_attaches_moderation_notes(monkeypatch) -> None:
    from apps.administration.services import user_management_service as ums
    from common.enums import ReportEntityType

    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        email=f"list_notes_{user_id}@example.com",
        firebase_uid=f"fb-{user_id}",
        status=UserStatus.suspended,
    )

    class _Row:
        User = user
        Profile = None
        university_name = None
        country_name = None

    class _Result:
        def all(self):
            return [_Row()]

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Result())

    with (
        patch.object(
            ums,
            "build_user_base_response",
            AsyncMock(return_value={"id": str(user_id), "email": user.email}),
        ),
        patch(
            "apps.moderation.repositories.get_latest_comments_by_entity_ids",
            AsyncMock(return_value={user_id: "Spam / harassment"}),
        ) as get_notes,
    ):
        items = await ums._fetch_users_with_details(db, page=1, page_size=10, role="user")

    assert items[0]["moderation_notes"] == "Spam / harassment"
    get_notes.assert_awaited_once()
    assert get_notes.await_args.kwargs["entity_type"] == ReportEntityType.user
    assert get_notes.await_args.kwargs["entity_ids"] == [user_id]


@pytest.mark.asyncio
async def test_admin_get_user_includes_latest_moderation_notes() -> None:
    from apps.administration.services import user_management_service as svc
    from apps.moderation.db_models import ModerationHistory
    from common.enums import ReportEntityType

    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        email=f"notes_{user_id}@example.com",
        firebase_uid=f"fb-{user_id}",
        status=UserStatus.suspended,
    )
    history = ModerationHistory(
        entity_type=ReportEntityType.user,
        entity_id=user_id,
        action="suspended",
        comment="Spam / harassment",
    )

    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = user
    profile_result = MagicMock()
    profile_result.scalar_one_or_none.return_value = None
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[user_result, profile_result])

    with (
        patch.object(svc, "build_user_base_response", AsyncMock(return_value={"id": str(user_id)})),
        patch(
            "apps.moderation.repositories.get_latest",
            AsyncMock(return_value=history),
        ) as get_latest,
    ):
        result = await svc.admin_get_user(str(user_id), db)

    assert result["user"]["moderation_notes"] == "Spam / harassment"
    get_latest.assert_awaited_once()
    assert get_latest.await_args.kwargs["entity_type"] == ReportEntityType.user
    assert get_latest.await_args.kwargs["entity_id"] == user_id


@pytest.mark.asyncio
async def test_admin_get_user_moderation_notes_null_when_no_history() -> None:
    from apps.administration.services import user_management_service as svc
    from common.enums import ReportEntityType

    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        email=f"no_notes_{user_id}@example.com",
        firebase_uid=f"fb-{user_id}",
        status=UserStatus.active,
    )

    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = user
    profile_result = MagicMock()
    profile_result.scalar_one_or_none.return_value = None
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[user_result, profile_result])

    with (
        patch.object(svc, "build_user_base_response", AsyncMock(return_value={"id": str(user_id)})),
        patch(
            "apps.moderation.repositories.get_latest",
            AsyncMock(return_value=None),
        ) as get_latest,
    ):
        result = await svc.admin_get_user(str(user_id), db)

    assert result["user"]["moderation_notes"] is None
    get_latest.assert_awaited_once()
    assert get_latest.await_args.kwargs["entity_type"] == ReportEntityType.user


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
                from apps.profiles.db_models.education_level_db_model import EducationLevel

                level = (
                    await session.execute(select(EducationLevel).where(EducationLevel.id == 2))
                ).scalar_one_or_none()
                if level is None:
                    session.add(EducationLevel(id=2, name="Masters", is_active=True))
                    await session.flush()
                interest = AcademicInterest(
                    name="Artificial Intelligence",
                    education_level_id=2,
                    is_active=True,
                )
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
            assert item1["country"] == str(country.id)
            assert item1["country_details"]["id"] == country.id
            assert item1["country_details"]["country_name"] == "United States"
            assert "Artificial Intelligence" in item1["academicInterests"]

            item2 = next(item for item in res_all["items"] if item["email"] == email2)
            assert item2["university"] is None
            assert item2["country"] is None
            assert item2["country_details"]["id"] is None
            assert item2["country_details"]["country_name"] is None
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
        monkeypatch.setattr("core.auth.services.delete_firebase_user", lambda *args: None)
        monkeypatch.setattr("core.auth.services.disable_firebase_user", lambda *args: None)
        monkeypatch.setattr("core.auth.services.enable_firebase_user", lambda *args: None)
        monkeypatch.setattr(
            "apps.notifications.services.notify_account_status",
            AsyncMock(return_value=None),
        )
        
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
            del_res = await admin_delete_user(str(user.id), "user", session)
            assert del_res["deleted"] is True
            assert del_res["status"] == "Deleting"
            assert "user" in del_res
            assert del_res["user"]["is_deleted"] is True
            db_user = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
            assert db_user.is_deleted is True
            
        # Test suspend user
        async with async_session_factory() as session:
            sus_res = await admin_update_user_status(
                str(user.id),
                AdminUserStatus.suspended,
                session,
                moderator_id=user.id,
                comment="Spam / harassment",
            )
            assert sus_res["status"] == "Suspended"
            
        # Test ban user
        async with async_session_factory() as session:
            ban_res = await admin_update_user_status(
                str(user.id),
                AdminUserStatus.banned,
                session,
                moderator_id=user.id,
                comment="Repeated abuse",
            )
            assert ban_res["status"] == "Banned"
            
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
            assert "Incorrect Username or Password." in res.message
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_admin_signin_non_superadmin() -> None:
    email = f"admin_user_{uuid.uuid4()}@example.com"
    user = User(
        email=email,
        password_hash=PASSWORD_HASHER.hash("AdminPassword123!"),
        status=UserStatus.active,
    )
    user.roles = []

    async def mock_execute(_stmt):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user
        return mock_result

    db = AsyncMock()
    db.execute = mock_execute

    payload = AdminLoginRequest(email=email, password="AdminPassword123!")
    with pytest.raises(HTTPException) as exc_info:
        await admin_signin(payload, db)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Forbidden: Admin access required"


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["superadmin", "moderator", "viewer"])
async def test_admin_signin_rejects_deleting_account(role: str) -> None:
    try:
        await init_db()
        email = f"admin_deleting_{role}_{uuid.uuid4()}@example.com"
        async with async_session_factory() as session:
            user = User(
                email=email,
                password_hash=PASSWORD_HASHER.hash("AdminPassword123!"),
                status=UserStatus.deleting,
                is_deleted=True,
                deleted_at=datetime.now(timezone.utc),
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)

            from apps.accounts.services import assign_user_role
            await assign_user_role(session, user, role)
            await session.commit()

        async with async_session_factory() as session:
            payload = AdminLoginRequest(email=email, password="AdminPassword123!")
            with pytest.raises(ApiError) as exc_info:
                await admin_signin(payload, session)
            assert exc_info.value.message == inactive_account_message(UserStatus.deleting)
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

        # Generate tokens bound to an independent session UUID
        session_id = uuid.uuid4()
        access_token, refresh_token = _generate_admin_tokens(user, session_id=session_id)
        
        # Decode access token and verify exp + independent session_id
        decoded_access = jwt.decode(access_token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        exp_time = datetime.fromtimestamp(decoded_access["exp"], tz=timezone.utc)
        now = datetime.now(timezone.utc)
        diff = exp_time - now
        assert abs(diff.total_seconds() - (ACCESS_TOKEN_EXPIRE_MINUTES * 60)) < 60
        assert decoded_access["session_id"] == str(session_id)
        assert decoded_access["session_id"] != str(user.id)

        # Decode refresh token and verify exp is NOT in refresh payload
        decoded_refresh = jwt.decode(refresh_token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        assert "exp" not in decoded_refresh
        assert decoded_refresh["session_id"] == str(session_id)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_admin_update_user_status_history_then_notify_then_firebase() -> None:
    """Status PATCH order: history commit → notify → Firebase disable."""
    from apps.administration.services import user_management_service as svc

    user_id = uuid.uuid4()
    moderator_id = uuid.uuid4()
    user = User(
        id=user_id,
        email=f"status_order_{user_id}@example.com",
        firebase_uid=f"fb-{user_id}",
        status=UserStatus.active,
    )

    call_order: list[str] = []

    async def _record(*_args, **_kwargs):
        call_order.append("history")
        return object()

    async def _notify(*_args, **_kwargs):
        call_order.append("notify")
        return None

    def _disable(_uid: str):
        call_order.append("firebase")

    db = AsyncMock()
    db_result = MagicMock()
    db_result.scalar_one_or_none.return_value = user
    db_result.first.return_value = None
    db.execute = AsyncMock(return_value=db_result)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    with (
        patch.object(svc, "build_user_base_response", AsyncMock(return_value={"userId": str(user_id)})),
        patch(
            "apps.moderation.services.record_moderation_history",
            AsyncMock(side_effect=_record),
        ) as history,
        patch(
            "apps.notifications.services.notify_account_status",
            AsyncMock(side_effect=_notify),
        ) as notify,
        patch(
            "apps.administration.services.admin_activity_log_service.create_admin_activity_log",
            AsyncMock(),
        ),
        patch(
            "core.auth.services.disable_firebase_user",
            side_effect=_disable,
        ),
        patch(
            "core.auth.services.enable_firebase_user",
            lambda *_a, **_k: None,
        ),
    ):
        result = await svc.admin_update_user_status(
            str(user_id),
            AdminUserStatus.suspended,
            db,
            moderator_id=moderator_id,
            comment="Spam / harassment",
        )

    assert result["status"] == "Suspended"
    assert call_order == ["history", "notify", "firebase"]
    history.assert_awaited_once()
    assert history.await_args.kwargs["action"] == "suspended"
    assert history.await_args.kwargs["comment"] == "Spam / harassment"
    assert history.await_args.kwargs["moderator_id"] == moderator_id
    notify.assert_awaited_once()
    assert notify.await_args.kwargs["reason"] == "Spam / harassment"
    assert notify.await_args.kwargs["sender_user_id"] == moderator_id
    assert user.status == UserStatus.suspended


@pytest.mark.asyncio
async def test_admin_update_user_status_clears_queue_counts() -> None:
    from common.enums import ReportEntityType
    from apps.administration.services import user_management_service as svc
    user_id = uuid.uuid4()
    moderator_id = uuid.uuid4()
    user = User(id=user_id, email="reported_user@example.com", status=UserStatus.active)
    user.roles = []

    db = AsyncMock()
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = user
    exec_result.first.return_value = None
    db.execute.return_value = exec_result

    with (
        patch.object(svc, "build_user_base_response", AsyncMock(return_value={"userId": str(user_id)})),
        patch("apps.report.repositories.report_repository.clear_entity_report_queue_counts", AsyncMock()) as clear_counts,
        patch("apps.moderation.services.record_moderation_history", AsyncMock()),
        patch("apps.notifications.services.notify_account_status", AsyncMock()),
        patch("apps.administration.services.admin_activity_log_service.create_admin_activity_log", AsyncMock()),
        patch("core.auth.services.disable_firebase_user", lambda *_a, **_k: None),
    ):
        result = await admin_update_user_status(
            str(user_id),
            AdminUserStatus.banned,
            db,
            moderator_id=moderator_id,
            comment="Banned for reports",
        )

    assert result["status"] == "Banned"
    clear_counts.assert_awaited_once_with(
        db,
        entity_type=ReportEntityType.user,
        entity_id=user_id,
    )


@pytest.mark.asyncio
async def test_admin_signup_rejects_role_not_in_db(monkeypatch) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(
        "core.auth.config.settings",
        SimpleNamespace(is_disposable_email_enabled=False),
    )

    payload = AdminSignupRequest(
        firstName="Admin",
        lastName="User",
        email=f"admin_{uuid.uuid4()}@example.com",
        password="Secret123",
        role="superadmin",
    )

    async def mock_execute(_stmt):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        return mock_result

    db = AsyncMock()
    db.execute = mock_execute

    with pytest.raises(HTTPException) as exc:
        await admin_signup(payload, db)

    assert exc.value.status_code == 401
    assert exc.value.detail == "Forbidden: Admin access required"


@pytest.mark.asyncio
async def test_admin_signup_rejects_non_staff_role(monkeypatch) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(
        "core.auth.config.settings",
        SimpleNamespace(is_disposable_email_enabled=False),
    )

    payload = AdminSignupRequest(
        firstName="Admin",
        lastName="User",
        email=f"admin_{uuid.uuid4()}@example.com",
        password="Secret123",
        role="user",
    )

    async def mock_execute(_stmt):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        return mock_result

    db = AsyncMock()
    db.execute = mock_execute

    with pytest.raises(HTTPException) as exc:
        await admin_signup(payload, db)

    assert exc.value.status_code == 401
    assert exc.value.detail == "Forbidden: Admin access required"

