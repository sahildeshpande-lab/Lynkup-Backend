from __future__ import annotations

import pytest
import uuid
import jwt
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException
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
    get_current_superadmin,
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
        with pytest.raises(HTTPException) as exc:
            await get_current_user(None, None)
        assert exc.value.status_code == 401
        assert exc.value.detail == "Missing access token"

        # 2. Invalid signature
        creds_invalid = HTTPAuthorizationCredentials(scheme="Bearer", credentials="invalid-sig-token")
        async with async_session_factory() as session:
            with pytest.raises(HTTPException) as exc:
                await get_current_user(creds_invalid, session)
            assert exc.value.status_code == 401
            assert exc.value.detail == "Invalid access token"

        # 3. Wrong token type (refresh instead of access)
        payload_refresh = {
            "sub": str(uuid.uuid4()),
            "type": "refresh",
            "exp": int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp())
        }
        token_refresh = jwt.encode(payload_refresh, auth_settings.jwt_secret, algorithm=auth_settings.jwt_algorithm)
        creds_refresh = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token_refresh)
        async with async_session_factory() as session:
            with pytest.raises(HTTPException) as exc:
                await get_current_user(creds_refresh, session)
            assert exc.value.status_code == 401
            assert exc.value.detail == "Invalid access token"

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
            with pytest.raises(HTTPException) as exc:
                await get_current_user(creds_access, session)
            assert exc.value.status_code == 403
            assert exc.value.detail == "Account deleted"

        # 3. User suspended state (not active)
        async with async_session_factory() as session:
            stmt = select(User).where(User.id == user_uuid)
            db_u = (await session.execute(stmt)).scalar_one()
            db_u.deleted_at = None
            db_u.status = UserStatus.suspended
            session.add(db_u)
            await session.commit()

        async with async_session_factory() as session:
            with pytest.raises(HTTPException) as exc:
                await get_current_user(creds_access, session)
            assert exc.value.status_code == 403
            assert exc.value.detail == "Account is not active"

    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_get_current_superadmin() -> None:
    # 1. Normal user should raise Forbidden
    user_normal = User()
    user_normal.role = "user"
    with pytest.raises(HTTPException) as exc:
        await get_current_superadmin(user_normal)
    assert exc.value.status_code == 403

    # 2. Superadmin should pass
    user_admin = User()
    user_admin.role = "superadmin"
    res = await get_current_superadmin(user_admin)
    assert res == user_admin
