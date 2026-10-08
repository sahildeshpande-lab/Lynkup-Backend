from __future__ import annotations

import pytest
import uuid
import jwt
from datetime import datetime, timezone, timedelta
from common.exceptions import ApiError
from fastapi.security import HTTPAuthorizationCredentials
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession
from core.database.session import async_session_factory, engine
from core.database.init import init_db

from apps.accounts.db_models import User
from common.enums import UserStatus
from apps.administration.services.auth_service import _generate_admin_tokens
from apps.administration.services.password_service import PASSWORD_HASHER
from core.auth.config import settings as auth_settings
from core.security.auth import (
    get_bearer_token,
    get_current_user,
    get_current_admin,
    get_current_superadmin,
    get_current_app_user,
    get_current_moderator,
    get_current_moderator_or_viewer,
)

@pytest.mark.asyncio
async def test_get_bearer_token() -> None:
    # 1. No credentials
    assert get_bearer_token(None) is None

    # 2. Valid credentials
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="token123")
    assert get_bearer_token(creds) == "token123"


@pytest.mark.asyncio
async def test_get_current_user_failures() -> None:
    try:
        await init_db()
        
        # 1. Missing access token
        with pytest.raises(ApiError) as exc:
            await get_current_user(None, None)
        assert exc.value.message == "Missing access token"

        # 2. Invalid signature
        creds_invalid = HTTPAuthorizationCredentials(scheme="Bearer", credentials="invalid-sig-token")
        async with async_session_factory() as session:
            with pytest.raises(ApiError) as exc:
                await get_current_user(creds_invalid, session)
            assert exc.value.message == "Invalid access token"

        # 3. Wrong token type (refresh instead of access)
        payload_refresh = {
            "sub": str(uuid.uuid4()),
            "type": "refresh",
            "exp": int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp())
        }
        token_refresh = jwt.encode(payload_refresh, auth_settings.jwt_secret, algorithm=auth_settings.jwt_algorithm)
        creds_refresh = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token_refresh)
        async with async_session_factory() as session:
            with pytest.raises(ApiError) as exc:
                await get_current_user(creds_refresh, session)
            assert exc.value.message == "Invalid access token"

    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_get_current_user_db_states(monkeypatch) -> None:
    try:
        await init_db()

        firebase_uid = f"uid-{uuid.uuid4()}"
        # Seed user
        async with async_session_factory() as session:
            user_uuid = uuid.uuid4()
            user = User(
                id=user_uuid,
                firebase_uid=firebase_uid,
                email="user_auth_sec_test@example.com",
                role="user",
                status="active",
            )
            session.add(user)
            await session.commit()

        monkeypatch.setattr(
            "core.auth.services.verify_firebase_token",
            lambda token, check_revoked=True: {
                "uid": firebase_uid,
                "email": "user_auth_sec_test@example.com",
            },
        )
        creds_access = HTTPAuthorizationCredentials(scheme="Bearer", credentials="firebase-id-token")

        # 1. Success active user (Firebase idToken)
        async with async_session_factory() as session:
            db_user = await get_current_user(creds_access, session)
            assert db_user.id == user_uuid

        # 2. User deleted state
        async with async_session_factory() as session:
            stmt = select(User).where(User.id == user_uuid)
            db_u = (await session.execute(stmt)).scalar_one()
            db_u.deleted_at = datetime.now(timezone.utc)
            session.add(db_u)
            await session.commit()

        async with async_session_factory() as session:
            with pytest.raises(ApiError) as exc:
                await get_current_user(creds_access, session)
            assert exc.value.message == "Account doesn't exist"

        # 3. User suspended state (not active)
        async with async_session_factory() as session:
            stmt = select(User).where(User.id == user_uuid)
            db_u = (await session.execute(stmt)).scalar_one()
            db_u.deleted_at = None
            db_u.status = UserStatus.suspended
            session.add(db_u)
            await session.commit()

        async with async_session_factory() as session:
            with pytest.raises(ApiError) as exc:
                await get_current_user(creds_access, session)
            assert exc.value.message == "Your account is suspended"

    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_get_current_superadmin() -> None:
    from common.exceptions import ApiError

    user_normal = User()
    user_normal.role = "user"
    with pytest.raises(ApiError):
        await get_current_superadmin(user_normal)

    user_admin = User()
    user_admin.role = "superadmin"
    res = await get_current_superadmin(user_admin)
    assert res == user_admin

    user_moderator = User()
    user_moderator.role = "moderator"
    with pytest.raises(ApiError):
        await get_current_superadmin(user_moderator)


@pytest.mark.asyncio
async def test_get_current_app_user() -> None:
    from common.exceptions import ApiError

    user = User()
    user.role = "user"
    assert await get_current_app_user(user) == user

    moderator = User()
    moderator.role = "moderator"
    with pytest.raises(ApiError):
        await get_current_app_user(moderator)


@pytest.mark.asyncio
async def test_get_current_moderator() -> None:
    from common.exceptions import ApiError

    moderator = User()
    moderator.role = "moderator"
    assert await get_current_moderator(moderator) == moderator

    superadmin = User()
    superadmin.role = "superadmin"
    assert await get_current_moderator(superadmin) == superadmin

    user = User()
    user.role = "user"
    with pytest.raises(ApiError):
        await get_current_moderator(user)


@pytest.mark.asyncio
async def test_get_current_moderator_or_viewer() -> None:
    from common.exceptions import ApiError

    moderator = User()
    moderator.role = "moderator"
    assert await get_current_moderator_or_viewer(moderator) == moderator

    superadmin = User()
    superadmin.role = "superadmin"
    assert await get_current_moderator_or_viewer(superadmin) == superadmin

    viewer = User()
    viewer.role = "viewer"
    assert await get_current_moderator_or_viewer(viewer) == viewer

    user = User()
    user.role = "user"
    with pytest.raises(ApiError):
        await get_current_moderator_or_viewer(user)


@pytest.mark.asyncio
async def test_get_current_admin_paths(monkeypatch) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from apps.administration.db_models import AdminSessionStatus
    from apps.administration.services.auth_service import _admin_password_fingerprint

    admin_id = uuid.uuid4()
    session_id = uuid.uuid4()
    password_hash = PASSWORD_HASHER.hash("AdminPassword123!")
    admin = User(
        id=admin_id,
        email=f"admin_auth_{uuid.uuid4()}@example.com",
        password_hash=password_hash,
        status=UserStatus.active,
    )
    admin.role = "superadmin"
    admin_session = SimpleNamespace(
        id=session_id,
        user_id=admin_id,
        status=AdminSessionStatus.ACTIVE.value,
    )

    access_token, _refresh_token, _jti = _generate_admin_tokens(admin, session_id=session_id)
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=access_token)
    request = SimpleNamespace(state=SimpleNamespace())

    results = iter([admin, admin_session])

    async def _execute(_stmt, *args, **kwargs):
        value = next(results)
        mock = MagicMock()
        mock.scalar_one_or_none.return_value = value
        return mock

    db = MagicMock()
    db.execute = AsyncMock(side_effect=_execute)

    db_admin = await get_current_admin(request, creds, db)
    assert db_admin.id == admin_id
    assert request.state.admin_session_id == session_id

    with pytest.raises(ApiError) as exc:
        await get_current_admin(SimpleNamespace(state=SimpleNamespace()), None, None)
    assert exc.value.message == "Missing access token"

    refresh_payload = {
        "sub": str(admin_id),
        "type": "refresh",
        "session_id": str(session_id),
        "pf": _admin_password_fingerprint(password_hash),
        "exp": int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp()),
    }
    refresh_token = jwt.encode(
        refresh_payload, auth_settings.jwt_secret, algorithm=auth_settings.jwt_algorithm
    )
    refresh_creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=refresh_token)
    with pytest.raises(ApiError) as exc:
        await get_current_admin(SimpleNamespace(state=SimpleNamespace()), refresh_creds, db)
    assert exc.value.message == "Invalid access token"

    plain_user = User(
        id=uuid.uuid4(),
        email=f"plain_user_{uuid.uuid4()}@example.com",
        status=UserStatus.active,
    )
    plain_user.role = "user"
    user_payload = {
        "sub": str(plain_user.id),
        "type": "access",
        "session_id": str(uuid.uuid4()),
        "exp": int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp()),
    }
    user_token = jwt.encode(
        user_payload, auth_settings.jwt_secret, algorithm=auth_settings.jwt_algorithm
    )
    user_creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=user_token)

    user_db = MagicMock()
    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = plain_user
    user_db.execute = AsyncMock(return_value=user_result)
    with pytest.raises(ApiError) as exc:
        await get_current_admin(SimpleNamespace(state=SimpleNamespace()), user_creds, user_db)
    assert exc.value.message == "Insufficient permissions"


@pytest.mark.asyncio
async def test_get_current_user_firebase_paths(monkeypatch) -> None:
    try:
        await init_db()

        firebase_uid = f"firebase-path-{uuid.uuid4()}"

        def _mock_verify(_token, check_revoked=False):
            return {"uid": firebase_uid, "email": "firebase@example.com"}

        monkeypatch.setattr("core.auth.services.verify_firebase_token", _mock_verify)

        async with async_session_factory() as session:
            user = User(
                firebase_uid=firebase_uid,
                email="firebase@example.com",
                role="user",
                status="active",
            )
            session.add(user)
            await session.commit()

        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="firebase-token")

        async with async_session_factory() as session:
            db_user = await get_current_user(creds, session)
            assert db_user.firebase_uid == firebase_uid

        async with async_session_factory() as session:
            stmt = select(User).where(User.firebase_uid == firebase_uid)
            db_u = (await session.execute(stmt)).scalar_one()
            db_u.status = UserStatus.banned
            session.add(db_u)
            await session.commit()

        async with async_session_factory() as session:
            with pytest.raises(ApiError) as exc:
                await get_current_user(creds, session)
            assert exc.value.message == "Your account is banned "

    finally:
        await engine.dispose()
