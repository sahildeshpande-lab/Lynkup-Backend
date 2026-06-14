import pytest
import pytest_asyncio
import uuid
from datetime import datetime, timezone
from sqlmodel import select, delete
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

@pytest.mark.asyncio
async def test_email_queue_and_cron_worker(db_session: AsyncSession, monkeypatch):
    from core.email_service import cron_send_emails, _send_email
    from apps.accounts.db_models import TransactionalEmailLog
    import asyncio

    # Clean up any existing logs
    await db_session.execute(delete(TransactionalEmailLog))
    await db_session.commit()

    # Mock SendGrid call
    sent_grid_calls = []
    async def mock_actually_send(to_email, subject, html_body, from_email):
        sent_grid_calls.append((to_email, subject, html_body, from_email))
        return True
    monkeypatch.setattr("core.email_service._actually_send_email_via_sendgrid", mock_actually_send)

    # Mock asyncio.sleep to raise CancelledError so loop runs only once
    async def mock_sleep(delay):
        raise asyncio.CancelledError()
    monkeypatch.setattr(asyncio, "sleep", mock_sleep)

    # Queue an email
    success = await _send_email(
        to_email="test_queue@example.com",
        subject="Test Queue Email",
        html_body="Hello queue",
        purpose="testing",
    )
    assert success is True

    # Verify queued in DB
    stmt = select(TransactionalEmailLog).where(TransactionalEmailLog.to == "test_queue@example.com")
    email_log = (await db_session.execute(stmt)).scalar_one()
    assert email_log.is_sent is False

    # Run cron
    await cron_send_emails()

    # Verify marked as sent
    await db_session.refresh(email_log)
    assert email_log.is_sent is True
    assert len(sent_grid_calls) == 1
    assert sent_grid_calls[0][0] == "test_queue@example.com"
    assert sent_grid_calls[0][1] == "Test Queue Email"
    assert sent_grid_calls[0][2] == "Hello queue"

@pytest.mark.asyncio
async def test_verify_email_endpoint(db_session: AsyncSession):
    from apps.accounts.services import verify_email
    
    # Create pending user
    uid = str(uuid.uuid4())
    email = f"pending_link_{uid[:8]}@example.com"
    otp = "verify_link_123"
    user = User(
        firebase_uid=uid,
        email=email,
        status=UserStatus.pending,
        email_otp=otp,
        email_otp_created_at=datetime.now(timezone.utc),
        onboarding_status="not_started",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.commit()

    # Verify
    response = await verify_email(token=otp, db=db_session)
    
    # Assert html response
    assert response.status_code == 200
    assert "Email Verified Successfully" in response.body.decode()

    # Verify DB updates
    refreshed = await db_session.get(User, user.id)
    assert refreshed.status == UserStatus.active
    assert refreshed.email_verified_at is not None
    assert refreshed.email_otp == "true"

@pytest.mark.asyncio
async def test_refresh_token_never_expires(db_session: AsyncSession):
    from apps.accounts.services import _generate_tokens
    from sqlalchemy.orm import selectinload
    
    uid = str(uuid.uuid4())
    email = f"token_{uid[:8]}@example.com"
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

    # Load roles eagerly to avoid MissingGreenlet
    stmt_user = select(User).options(selectinload(User.roles)).where(User.id == user.id)
    user = (await db_session.execute(stmt_user)).scalar_one()

    access_token, refresh_token = _generate_tokens(user)
    
    import jwt
    from apps.accounts.services import JWT_SECRET, JWT_ALGORITHM
    
    decoded = jwt.decode(refresh_token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    assert "exp" not in decoded
