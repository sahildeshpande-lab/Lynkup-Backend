import pytest
import pytest_asyncio
import uuid
from datetime import datetime, timezone
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession
from core.db.session import async_session_factory
from core.db.init import init_db

from apps.accounts.services import login, logout, logout_all
from apps.accounts.db_models import User, Role
from common.enums import UserStatus

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

@pytest.mark.asyncio
async def test_login_active_user(db_session: AsyncSession):
    # Create an active user
    uid = str(uuid.uuid4())
    email = f"active_{uid[:8]}@example.com"
    user = User(
        firebase_uid=uid,
        email=email,
        status=UserStatus.active,
        onboarding_status="not_started",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.commit()

    firebase_claims = {"uid": uid, "email": email}
    response = await login(firebase_user=firebase_claims, db=db_session)

    assert response.status is True
    assert response.message == "Login successful"
    assert response.data["emailSent"] is False
    assert response.data["user"]["email"] == email
    assert response.data["user"]["status"] == "active"

@pytest.mark.asyncio
async def test_login_pending_user_sends_otp(db_session: AsyncSession, monkeypatch):
    # Mock email sending
    sent_emails = []
    async def mock_send_otp_email(to_email, otp, otp_purpose):
        sent_emails.append((to_email, otp, otp_purpose))
        return True
    monkeypatch.setattr("apps.accounts.services.send_otp_email", mock_send_otp_email)

    # Create a pending user
    uid = str(uuid.uuid4())
    email = f"pending_{uid[:8]}@example.com"
    user = User(
        firebase_uid=uid,
        email=email,
        status=UserStatus.pending,
        onboarding_status="not_started",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.commit()

    firebase_claims = {"uid": uid, "email": email}
    response = await login(firebase_user=firebase_claims, db=db_session)

    assert response.status is True
    assert response.message == "Verification email sent. Please verify your OTP."
    assert response.data["emailSent"] is True

    # Refresh user to verify OTP fields were populated in DB
    refreshed = await db_session.get(User, user.id)
    assert refreshed.email_otp is not None
    assert refreshed.email_otp_created_at is not None

    # Verify email sending mock called with correct details
    assert len(sent_emails) == 1
    assert sent_emails[0][0] == email
    assert sent_emails[0][1] == refreshed.email_otp
    assert sent_emails[0][2] == "email_verification"

@pytest.mark.asyncio
async def test_logout_sets_pending(db_session: AsyncSession):
    # Create an active user
    uid = str(uuid.uuid4())
    email = f"user_{uid[:8]}@example.com"
    user = User(
        firebase_uid=uid,
        email=email,
        status=UserStatus.active,
        onboarding_status="not_started",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.commit()

    from apps.accounts.schemas import LogoutRequest
    payload = LogoutRequest()
    response = await logout(payload=payload, db=db_session, current_user=user)

    assert response == {"logged_out": True}

    # Verify status changed to pending
    refreshed = await db_session.get(User, user.id)
    assert refreshed.status == UserStatus.pending

@pytest.mark.asyncio
async def test_logout_all_sets_pending(db_session: AsyncSession):
    # Create an active user
    uid = str(uuid.uuid4())
    email = f"user_{uid[:8]}@example.com"
    user = User(
        firebase_uid=uid,
        email=email,
        status=UserStatus.active,
        onboarding_status="not_started",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.commit()

    response = await logout_all(current_user=user, db=db_session)

    assert response == {"logged_out_all": True}

    # Verify status changed to pending
    refreshed = await db_session.get(User, user.id)
    assert refreshed.status == UserStatus.pending
