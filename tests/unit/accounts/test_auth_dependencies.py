# tests/unit/test_auth_dependencies.py
"""Tests for the new Firebase‑only authentication dependency.

Covers:
- Existing user lookup
- Auto‑provision of a new user
- Deleted & inactive account handling
- Throttled last_login_at update
- Security event creation (basic sanity – record count)
"""

import pytest
import pytest_asyncio
import uuid
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
from sqlmodel import SQLModel, select
from sqlalchemy.ext.asyncio import AsyncSession
from core.database.session import async_session_factory, engine
from core.database.init import init_db

from core.auth.dependencies import (
    get_current_user,
    provision_user_from_firebase,
    log_security_event,
)
from apps.accounts.db_models import User, Role, UserRole, SecurityEvent, SecurityEventType
from apps.accounts.services import assign_user_role

# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(autouse=True)
async def prepare_db():
    await init_db()
    # Insert default roles if they don't exist
    async with async_session_factory() as session:
        for role_name in ["user", "superadmin", "moderator", "viewer"]:
            stmt = select(Role).where(Role.name == role_name)
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if not existing:
                role = Role(name=role_name, description=f"{role_name} role")
                session.add(role)
        await session.commit()

@pytest_asyncio.fixture
async def db_session():
    async with async_session_factory() as session:
        yield session
        await session.rollback()

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_existing_user_lookup_and_login_throttle(db_session: AsyncSession):
    # Arrange – create a user with a stale last_login_at
    uid = str(uuid.uuid4())
    email = f"existing_{uid[:8]}@example.com"
    user = User(
        firebase_uid=uid,
        
        email=email,
        status="active",
        onboarding_status="not_started",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        last_login_at=datetime.now(timezone.utc) - timedelta(minutes=10),
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.commit()
    # Act
    original_last_login = user.last_login_at
    firebase_claims = {"uid": uid, "email": email, "name": f"Existing User {uid[:8]}"}
    returned_user = await get_current_user(firebase_user=firebase_claims, db=db_session)
    # Assert – same user object returned
    assert returned_user.id == user.id
    # The login timestamp should have been refreshed (throttled condition true)
    refreshed = await db_session.get(User, user.id)
    assert refreshed.last_login_at > original_last_login
    # A LOGIN_SUCCESS event should exist
    stmt = select(SecurityEvent).where(SecurityEvent.user_id == user.id)
    events = (await db_session.execute(stmt)).scalars().all()
    assert any(e.event_type == SecurityEventType.LOGIN_SUCCESS for e in events)

@pytest.mark.asyncio
async def test_auto_provision_new_user(db_session: AsyncSession):
    uid = str(uuid.uuid4())
    email = f"new_{uid[:8]}@example.com"
    firebase_claims = {"uid": uid, "email": email, "name": f"New User {uid[:8]}"}
    # Act – call get_current_user which should create the user
    new_user = await get_current_user(firebase_user=firebase_claims, db=db_session)
    # Assert – user fields populated correctly
    assert new_user.firebase_uid == uid
    
    # Role assignment should have created a UserRole linking to the "user" role
    stmt = select(UserRole).where(UserRole.user_id == new_user.id)
    role_links = (await db_session.execute(stmt)).scalars().all()
    assert len(role_links) == 1
    # Verify the linked role is the default "user"
    linked_role = await db_session.get(Role, role_links[0].role_id)
    assert linked_role.name == "user"
    # Security event of type LOGIN_SUCCESS must be present
    stmt = select(SecurityEvent).where(SecurityEvent.user_id == new_user.id)
    events = (await db_session.execute(stmt)).scalars().all()
    assert any(e.event_type == SecurityEventType.LOGIN_SUCCESS for e in events)

@pytest.mark.asyncio
async def test_deleted_account_raises_403(db_session: AsyncSession):
    uid = str(uuid.uuid4())
    email = f"del_{uid[:8]}@example.com"
    user = User(
        firebase_uid=uid,
        
        email=email,
        status="active",
        onboarding_status="not_started",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        deleted_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.commit()
    firebase_claims = {"uid": uid, "email": email, "name": f"Deleted {uid[:8]}"}
    with pytest.raises(HTTPException) as exc:
        await get_current_user(firebase_user=firebase_claims, db=db_session)
    assert exc.value.status_code == 403
    assert "Account deleted" in exc.value.detail

@pytest.mark.asyncio
async def test_inactive_account_raises_403(db_session: AsyncSession):
    uid = str(uuid.uuid4())
    email = f"inactive_{uid[:8]}@example.com"
    user = User(
        firebase_uid=uid,
        email=email,
        status="suspended",
        onboarding_status="not_started",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.commit()
    firebase_claims = {"uid": uid, "email": email, "name": f"Inactive {uid[:8]}"}
    with pytest.raises(HTTPException) as exc:
        await get_current_user(firebase_user=firebase_claims, db=db_session)
    assert exc.value.status_code == 403
    assert "Account is suspended" in exc.value.detail

@pytest.mark.asyncio
async def test_login_throttle_prevents_unnecessary_update(db_session: AsyncSession):
    uid = str(uuid.uuid4())
    email = f"throttle_{uid[:8]}@example.com"
    now = datetime.now(timezone.utc)
    user = User(
        firebase_uid=uid,
        
        email=email,
        status="active",
        onboarding_status="not_started",
        created_at=now,
        updated_at=now,
        last_login_at=now,  # recent login
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.commit()
    firebase_claims = {"uid": uid, "email": email, "name": f"Throttle {uid[:8]}"}
    returned = await get_current_user(firebase_user=firebase_claims, db=db_session)
    # The timestamp should stay the same (no refresh)
    refreshed = await db_session.get(User, user.id)
    assert refreshed.last_login_at == now
    # No login events should have been logged (since throttled)
    stmt = select(SecurityEvent).where(SecurityEvent.user_id == user.id)
    events = (await db_session.execute(stmt)).scalars().all()
    assert len(events) == 0


@pytest.mark.asyncio
async def test_forgot_password_rate_limit(db_session: AsyncSession, monkeypatch):
    from apps.accounts.services import forgot_password
    from apps.accounts.schemas import ForgotPasswordRequest
    from apps.accounts.db_models import PasswordResetToken

    # Mock email service
    async def mock_send(*args, **kwargs):
        pass
    monkeypatch.setattr("apps.accounts.services.send_reset_password_email", mock_send)

    uid = str(uuid.uuid4())
    email = f"forgot_{uid[:8]}@example.com"
    now = datetime.now(timezone.utc)
    user = User(
        firebase_uid=uid,
        email=email,
        status="active",
        onboarding_status="not_started",
        created_at=now,
        updated_at=now,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.commit()

    payload = ForgotPasswordRequest(email=email, firebaseId="test-firebase-id")
    
    # First request
    res = await forgot_password(payload, db_session)
    assert res.status is True
    assert res.message == "Password reset link sent successfully to your mail"

    # Second request immediately (should trigger rate limit validation)
    from core.auth.config import settings as auth_settings
    res2 = await forgot_password(payload, db_session)
    assert res2.status is False
    assert res2.message == f"Recently email for reset password has been sent. Please try after {auth_settings.password_reset_token_expire_minutes} mins"


@pytest.mark.asyncio
async def test_credentials_or_401() -> None:
    from core.auth.dependencies import _credentials_or_401
    with pytest.raises(HTTPException) as exc:
        _credentials_or_401(None)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_firebase_user_dep(monkeypatch) -> None:
    from core.auth.dependencies import get_current_firebase_user
    from fastapi.security import HTTPAuthorizationCredentials

    # 1. Invalid token raises 401
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="bad-token")
    def mock_verify(*args, **kwargs):
        raise ValueError("invalid")
    monkeypatch.setattr("core.auth.dependencies.verify_firebase_token", mock_verify)
    with pytest.raises(HTTPException) as exc:
        await get_current_firebase_user(creds)
    assert exc.value.status_code == 401

    # 2. Valid token returns decoded user
    monkeypatch.setattr("core.auth.dependencies.verify_firebase_token", lambda *args, **kwargs: {"uid": "123"})
    res = await get_current_firebase_user(creds)
    assert res["uid"] == "123"


@pytest.mark.asyncio
async def test_get_current_revoked_checked_firebase_user(monkeypatch) -> None:
    from core.auth.dependencies import get_current_revoked_checked_firebase_user
    from fastapi.security import HTTPAuthorizationCredentials

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")
    monkeypatch.setattr("core.auth.dependencies.verify_firebase_token", lambda *args, **kwargs: {"uid": "123"})
    res = await get_current_revoked_checked_firebase_user(creds)
    assert res["uid"] == "123"


@pytest.mark.asyncio
async def test_require_recent_auth() -> None:
    from core.auth.dependencies import require_recent_auth
    
    # 1. Missing auth_time
    with pytest.raises(HTTPException) as exc:
        await require_recent_auth({"uid": "123"})
    assert exc.value.status_code == 401
    assert "Recent Firebase authentication required" in exc.value.detail

    # 2. Expired auth_time
    old_time = int(datetime.now(timezone.utc).timestamp()) - 100000
    with pytest.raises(HTTPException) as exc:
        await require_recent_auth({"uid": "123", "auth_time": old_time})
    assert exc.value.status_code == 401

    # 3. Valid recent auth_time
    recent_time = int(datetime.now(timezone.utc).timestamp()) - 10
    res = await require_recent_auth({"uid": "123", "auth_time": recent_time})
    assert res["uid"] == "123"


@pytest.mark.asyncio
async def test_get_firebase_user_from_payload(monkeypatch) -> None:
    from core.auth.dependencies import get_firebase_user_from_payload
    from fastapi import Request

    class MockRequest:
        def __init__(self, json_data=None, headers=None):
            self.json_data = json_data
            self.headers = headers or {}

        async def json(self):
            if self.json_data is None:
                raise ValueError("No json data")
            return self.json_data

    # 1. token in token_id payload
    req = MockRequest(json_data={"token_id": "test-token-123"})
    monkeypatch.setattr("core.auth.dependencies.verify_firebase_token", lambda token, **kwargs: {"uid": "user123", "token": token})
    res = await get_firebase_user_from_payload(req)
    assert res["token"] == "test-token-123"

    # 2. token in tokenId payload
    req = MockRequest(json_data={"tokenId": "test-token-456"})
    res = await get_firebase_user_from_payload(req)
    assert res["token"] == "test-token-456"

    # 3. token in headers fallback
    req = MockRequest(json_data=None, headers={"Authorization": "Bearer test-token-789"})
    res = await get_firebase_user_from_payload(req)
    assert res["token"] == "test-token-789"

    # 4. missing token raises 401
    req = MockRequest(json_data={})
    with pytest.raises(HTTPException) as exc:
        await get_firebase_user_from_payload(req)
    assert exc.value.status_code == 401


