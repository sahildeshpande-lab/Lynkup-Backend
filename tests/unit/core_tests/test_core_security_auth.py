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
from core.auth.config import settings as auth_settings
from core.security.auth import (
    get_bearer_token,
    get_current_user,
    get_current_admin,
    get_current_superadmin,
    get_current_app_user,
    get_current_moderator,
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
async def test_get_current_user_db_states() -> None:
    try:
        await init_db()

        # Seed user
        async with async_session_factory() as session:
            user_uuid = uuid.uuid4()
            user = User(
                id=user_uuid,
                firebase_uid=f"uid-{uuid.uuid4()}",
                email="user_auth_sec_test@example.com",
                role="user",
                status="active",
            )
            session.add(user)
            await session.commit()

        # Generate valid access token
        payload_access = {
            "sub": str(user_uuid),
            "type": "access",
            "exp": int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp())
        }
        token_access = jwt.encode(payload_access, auth_settings.jwt_secret, algorithm=auth_settings.jwt_algorithm)
        creds_access = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token_access)

        # 1. Success active user
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
            assert exc.value.message == "Account deleted"

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
            assert exc.value.message == "Account is suspended"

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
async def test_get_current_admin_paths() -> None:
    try:
        await init_db()

        async with async_session_factory() as session:
            admin_id = uuid.uuid4()
            admin = User(
                id=admin_id,
                email=f"admin_auth_{uuid.uuid4()}@example.com",
                role="superadmin",
                status="active",
            )
            session.add(admin)
            await session.commit()
            await session.refresh(admin)

            from apps.accounts.services import assign_user_role
            await assign_user_role(session, admin, "superadmin")
            await session.commit()

        access_payload = {
            "sub": str(admin_id),
            "type": "access",
            "exp": int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp()),
        }
        access_token = jwt.encode(
            access_payload, auth_settings.jwt_secret, algorithm=auth_settings.jwt_algorithm
        )
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=access_token)

        async with async_session_factory() as session:
            db_admin = await get_current_admin(creds, session)
            assert db_admin.id == admin_id

        with pytest.raises(ApiError) as exc:
            await get_current_admin(None, None)
        assert exc.value.message == "Missing access token"

        refresh_payload = {
            "sub": str(admin_id),
            "type": "refresh",
            "exp": int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp()),
        }
        refresh_token = jwt.encode(
            refresh_payload, auth_settings.jwt_secret, algorithm=auth_settings.jwt_algorithm
        )
        refresh_creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=refresh_token)
        async with async_session_factory() as session:
            with pytest.raises(ApiError) as exc:
                await get_current_admin(refresh_creds, session)
            assert exc.value.message == "Invalid access token"

        async with async_session_factory() as session:
            user = User(
                email=f"plain_user_{uuid.uuid4()}@example.com",
                role="user",
                status="active",
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)

            user_payload = {
                "sub": str(user.id),
                "type": "access",
                "exp": int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp()),
            }
            user_token = jwt.encode(
                user_payload, auth_settings.jwt_secret, algorithm=auth_settings.jwt_algorithm
            )
            user_creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=user_token)

            with pytest.raises(ApiError) as exc:
                await get_current_admin(user_creds, session)
            assert exc.value.message == "Insufficient permissions"

    finally:
        await engine.dispose()


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
                email=f"firebase_path_{uuid.uuid4()}@example.com",
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
            assert exc.value.message == "Account is banned"

    finally:
        await engine.dispose()
