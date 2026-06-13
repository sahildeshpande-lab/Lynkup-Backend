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
from core.db.session import async_session_factory, engine
from core.db.init import init_db

from core.auth.dependencies import (
    get_current_user,
    provision_user_from_firebase,
    log_security_event,
)
from apps.accounts.db_models import User, Role, UserRole, UserIdentity, SecurityEvent, SecurityEventType
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
    # Identity record should exist
    stmt_identity = select(UserIdentity).where(UserIdentity.user_id == new_user.id)
    identity = (await db_session.execute(stmt_identity)).scalar_one_or_none()
    assert identity is not None
    assert identity.provider == "firebase"
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
async def test_inactive_account_does_not_raise_403(db_session: AsyncSession):
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
    res_user = await get_current_user(firebase_user=firebase_claims, db=db_session)
    assert res_user.id == user.id

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
    monkeypatch.setattr("core.email_service.send_reset_password_email", mock_send)

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

    payload = ForgotPasswordRequest(email=email)
    
    # First request
    res = await forgot_password(payload, db_session)
    assert res.status is True
    assert res.message == "Password reset link sent successfully"

    # Second request immediately (should trigger rate limit validation)
    res2 = await forgot_password(payload, db_session)
    assert res2.status is False
    assert res2.message == "Recently email for resest password as been send please try after 15 mins  "

