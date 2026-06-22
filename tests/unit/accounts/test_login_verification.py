<<<<<<< HEAD
import pytest
import pytest_asyncio
import uuid
from datetime import datetime, timezone
from sqlmodel import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from core.database.session import async_session_factory
from core.database.init import init_db

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
    # Test login from a known/existing device (skips OTP)
    from apps.accounts.services import _hash_password
    from apps.accounts.schemas import LoginRequest
    from apps.accounts.db_models import UserInstallation
    uid = str(uuid.uuid4())
    email = f"active_{uid[:8]}@example.com"
    user = User(
        firebase_uid=uid,
        email=email,
        password_hash=_hash_password("ValidPassword123"),
        status=UserStatus.active,
        onboarding_status="not_started",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        email_verified_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    await db_session.flush()

    # Pre-insert UserInstallation record for this device
    installation = UserInstallation(
        user_id=user.id,
        device_id="known-device-id",
        platform=None,
        app_version=None,
        installed_at=datetime.now(timezone.utc),
        last_active_at=datetime.now(timezone.utc),
    )
    db_session.add(installation)
    await db_session.flush()
    await db_session.commit()

    payload = LoginRequest(
        email=email,
        password="ValidPassword123",
        firebaseId="valid-id-token",
        device_id="known-device-id",
    )
    firebase_claims = {"uid": uid, "email": email}
    response = await login(payload=payload, firebase_user=firebase_claims, db=db_session)

    assert response.status is True
    assert response.message == "Login successful"
    assert response.data["emailSent"] is False
    assert response.data["user"]["email"] == email
    assert response.data["user"]["status"] == "active"


@pytest.mark.asyncio
async def test_login_active_user_new_device(db_session: AsyncSession, monkeypatch):
    # Test login from a new device (requires OTP, sets user status to pending)
    from apps.accounts.services import _hash_password
    from apps.accounts.schemas import LoginRequest
    sent_emails = []
    async def mock_send_otp_email(to_email, otp, otp_purpose):
        sent_emails.append((to_email, otp, otp_purpose))
        return True
    monkeypatch.setattr("apps.accounts.services.send_otp_email", mock_send_otp_email)

    uid = str(uuid.uuid4())
    email = f"active_new_{uid[:8]}@example.com"
    user = User(
        firebase_uid=uid,
        email=email,
        password_hash=_hash_password("ValidPassword123"),
        status=UserStatus.active,
        onboarding_status="not_started",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        email_verified_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.commit()

    payload = LoginRequest(
        email=email,
        password="ValidPassword123",
        firebaseId="valid-id-token",
        device_id="new-device-id",
    )
    firebase_claims = {"uid": uid, "email": email}
    response = await login(payload=payload, firebase_user=firebase_claims, db=db_session)

    assert response.status is True
    assert response.message == "Verification email sent. Please verify your OTP."
    assert response.data["emailSent"] is True

    # Verify user status is now pending in DB
    refreshed = await db_session.get(User, user.id)
    assert refreshed.status == UserStatus.pending
    assert refreshed.email_otp is not None

    # Verify a UserInstallation was created for the new device
    from apps.accounts.db_models import UserInstallation
    from sqlmodel import select
    stmt = select(UserInstallation).where(UserInstallation.user_id == user.id, UserInstallation.device_id == "new-device-id")
    inst = (await db_session.execute(stmt)).scalar_one_or_none()
    assert inst is not None
    assert inst.platform is None


@pytest.mark.asyncio
async def test_login_pending_user_sends_otp(db_session: AsyncSession, monkeypatch):
    # Mock email sending
    from apps.accounts.services import _hash_password
    from apps.accounts.schemas import LoginRequest
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
        password_hash=_hash_password("ValidPassword123"),
        status=UserStatus.pending,
        onboarding_status="not_started",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.commit()

    payload = LoginRequest(
        email=email,
        password="ValidPassword123",
        firebaseId="valid-id-token",
        device_id="some-device-id",
    )
    firebase_claims = {"uid": uid, "email": email}
    response = await login(payload=payload, firebase_user=firebase_claims, db=db_session)

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
=======
"""
Unit tests for OTP verification, login flow helpers, and token-generation logic
in apps/accounts/services.py.
All DB calls use mock sessions — no real DB writes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import jwt
import pytest

from apps.accounts.schemas import OtpVerifyRequest, ResendOtpRequest
from common.enums import UserStatus, OnboardingStatus


# ---------------------------------------------------------------------------
# Helper: build a lightweight fake User object
# ---------------------------------------------------------------------------

def _make_user(
    email: str = "pytest.user@example.com",
    firebase_uid: str = "pytest-uid-123",
    status: UserStatus = UserStatus.active,
    otp: str = "123456",
    otp_age_seconds: int = 0,
) -> MagicMock:
    now = datetime.now(timezone.utc)
    user = MagicMock()
    user.id = uuid4()
    user.firebase_uid = firebase_uid
    user.email = email
    user.status = status
    user.email_otp = otp
    user.email_otp_created_at = now - timedelta(seconds=otp_age_seconds)
    user.email_verified_at = None
    user.deleted_at = None
    user.last_login_at = None
    user.created_at = now
    user.updated_at = now
    user.role = "user"
    user.roles = []
    return user


# ---------------------------------------------------------------------------
# _generate_otp
# ---------------------------------------------------------------------------

class TestGenerateOtp:
    def test_returns_six_digit_string(self):
        from apps.accounts.services import _generate_otp

        otp = _generate_otp()
        assert otp.isdigit()
        assert len(otp) == 6

    def test_otp_in_valid_range(self):
        from apps.accounts.services import _generate_otp

        otp = int(_generate_otp())
        assert 100000 <= otp <= 999999

    def test_generates_different_otps(self):
        from apps.accounts.services import _generate_otp

        otps = {_generate_otp() for _ in range(20)}
        # At least some variation expected in 20 calls
        assert len(otps) > 1


# ---------------------------------------------------------------------------
# _hash_password / _hash_token
# ---------------------------------------------------------------------------

class TestHashHelpers:
    def test_hash_password_returns_string(self):
        from apps.accounts.services import _hash_password

        hashed = _hash_password("Secret123")
        assert isinstance(hashed, str)
        assert hashed != "Secret123"

    def test_hash_token_is_deterministic(self):
        from apps.accounts.services import _hash_token

        h1 = _hash_token("my-refresh-token")
        h2 = _hash_token("my-refresh-token")
        assert h1 == h2

    def test_hash_token_different_for_different_input(self):
        from apps.accounts.services import _hash_token

        assert _hash_token("token-a") != _hash_token("token-b")


# ---------------------------------------------------------------------------
# _generate_tokens
# ---------------------------------------------------------------------------

class TestGenerateTokens:
    def test_returns_two_jwt_strings(self):
        from apps.accounts.services import _generate_tokens, JWT_SECRET, JWT_ALGORITHM

        user = _make_user()
        access, refresh = _generate_tokens(user)

        for tok in (access, refresh):
            assert isinstance(tok, str)
            assert tok.count(".") == 2  # JWT has 3 parts

    def test_access_token_type_is_access(self):
        from apps.accounts.services import _generate_tokens, JWT_SECRET, JWT_ALGORITHM

        user = _make_user()
        access, _ = _generate_tokens(user)
        payload = jwt.decode(access, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        assert payload["type"] == "access"

    def test_refresh_token_type_is_refresh(self):
        from apps.accounts.services import _generate_tokens, JWT_SECRET, JWT_ALGORITHM

        user = _make_user()
        _, refresh = _generate_tokens(user)
        payload = jwt.decode(refresh, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        assert payload["type"] == "refresh"

    def test_access_token_contains_user_id(self):
        from apps.accounts.services import _generate_tokens, JWT_SECRET, JWT_ALGORITHM

        user = _make_user()
        access, _ = _generate_tokens(user)
        payload = jwt.decode(access, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        assert payload["sub"] == str(user.id)


# ---------------------------------------------------------------------------
# verify_otp — unit tests via mock DB session
# ---------------------------------------------------------------------------

class TestVerifyOtp:
    @pytest.mark.asyncio
    async def test_user_not_found_returns_false(self):
        from apps.accounts.services import verify_otp

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

        payload = OtpVerifyRequest(email="pytest.otp@example.com", otp="123456")
        result = await verify_otp(payload, {"uid": "pytest-uid"}, mock_db)
        assert result.status is False
        assert "not found" in result.message.lower()

    @pytest.mark.asyncio
    async def test_invalid_otp_returns_false(self):
        from apps.accounts.services import verify_otp

        user = _make_user(otp="654321")
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=user)))

        payload = OtpVerifyRequest(email="pytest.otp@example.com", otp="000000")
        result = await verify_otp(payload, {"uid": "pytest-uid"}, mock_db)
        assert result.status is False
        assert "invalid" in result.message.lower()

    @pytest.mark.asyncio
    async def test_expired_otp_returns_false(self):
        from apps.accounts.services import verify_otp

        user = _make_user(otp="123456", otp_age_seconds=700)  # >10 mins
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=user)))

        payload = OtpVerifyRequest(email="pytest.otp@example.com", otp="123456")
        result = await verify_otp(payload, {"uid": "pytest-uid"}, mock_db)
        assert result.status is False
        assert "expired" in result.message.lower()


# ---------------------------------------------------------------------------
# resend_otp — unit tests via mock DB session
# ---------------------------------------------------------------------------

class TestResendOtp:
    @pytest.mark.asyncio
    async def test_user_not_found_returns_false(self):
        from apps.accounts.services import resend_otp

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

        payload = ResendOtpRequest(email="pytest.resend@example.com")
        result = await resend_otp(payload, {"uid": "pytest-uid"}, mock_db)
        assert result.status is False

    @pytest.mark.asyncio
    async def test_resend_otp_success(self):
        from apps.accounts.services import resend_otp

        user = _make_user(email="pytest.resend2@example.com")
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=user)))
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()

        payload = ResendOtpRequest(email="pytest.resend2@example.com")

        with patch("apps.accounts.services.send_otp_email", new_callable=AsyncMock):
            result = await resend_otp(payload, {"uid": "pytest-uid"}, mock_db)

        assert result.status is True
        assert "sent" in result.message.lower()


# ---------------------------------------------------------------------------
# login — unit tests via mock DB session
# ---------------------------------------------------------------------------

class TestLogin:
    @pytest.mark.asyncio
    async def test_missing_uid_returns_false(self):
        from apps.accounts.services import login

        mock_db = AsyncMock()
        result = await login(firebase_user={}, db=mock_db)
        assert result.status is False

    @pytest.mark.asyncio
    async def test_user_not_found_returns_false(self):
        from apps.accounts.services import login

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

        result = await login(firebase_user={"uid": "pytest-unknown-uid"}, db=mock_db)
        assert result.status is False
        assert "not found" in result.message.lower()

    @pytest.mark.asyncio
    async def test_deleted_user_returns_false(self):
        from apps.accounts.services import login
        from datetime import datetime, timezone

        user = _make_user()
        user.deleted_at = datetime.now(timezone.utc)
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=user)))

        result = await login(firebase_user={"uid": "pytest-del-uid"}, db=mock_db)
        assert result.status is False

    @pytest.mark.asyncio
    async def test_inactive_user_returns_false(self):
        from apps.accounts.services import login

        user = _make_user(status=UserStatus.suspended)
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=user)))

        result = await login(firebase_user={"uid": "pytest-susp-uid"}, db=mock_db)
        assert result.status is False


# ---------------------------------------------------------------------------
# forgot_password — unit tests via mock DB
# ---------------------------------------------------------------------------

class TestForgotPassword:
    @pytest.mark.asyncio
    async def test_user_not_found_returns_false(self):
        from apps.accounts.services import forgot_password
        from apps.accounts.schemas import ForgotPasswordRequest

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

        payload = ForgotPasswordRequest(email="pytest.forgot@example.com")
        result = await forgot_password(payload, mock_db)
        assert result.status is False
        assert "not found" in result.message.lower()


# ---------------------------------------------------------------------------
# _build_auth_user_response
# ---------------------------------------------------------------------------

class TestBuildAuthUserResponse:
    def test_with_profile(self):
        from apps.accounts.services import _build_auth_user_response

        user = _make_user()
        profile = MagicMock()
        profile.display_name = "pytest John Doe"
        profile.completeness_score = 50

        response = _build_auth_user_response(user, profile)
        assert response.firstName == "pytest"
        assert response.lastName == "John Doe"
        assert response.completenessScore == 50

    def test_without_profile(self):
        from apps.accounts.services import _build_auth_user_response

        user = _make_user()
        response = _build_auth_user_response(user, None)
        assert response.completenessScore == 33
        assert response.firstName == ""

    def test_email_set_correctly(self):
        from apps.accounts.services import _build_auth_user_response

        user = _make_user(email="pytest.build@example.com")
        response = _build_auth_user_response(user, None)
        assert response.email == "pytest.build@example.com"


# ---------------------------------------------------------------------------
# _registration_type_from_firebase
# ---------------------------------------------------------------------------

class TestRegistrationTypeFromFirebase:
    def test_google_provider(self):
        from apps.accounts.services import _registration_type_from_firebase
        from common.enums import RegistrationType

        result = _registration_type_from_firebase({"firebase": {"sign_in_provider": "google.com"}})
        assert result == RegistrationType.Google

    def test_apple_provider(self):
        from apps.accounts.services import _registration_type_from_firebase
        from common.enums import RegistrationType

        result = _registration_type_from_firebase({"firebase": {"sign_in_provider": "apple.com"}})
        assert result == RegistrationType.Apple

    def test_email_provider(self):
        from apps.accounts.services import _registration_type_from_firebase
        from common.enums import RegistrationType

        result = _registration_type_from_firebase({"firebase": {"sign_in_provider": "password"}})
        assert result == RegistrationType.email

    def test_missing_firebase_key(self):
        from apps.accounts.services import _registration_type_from_firebase
        from common.enums import RegistrationType

        result = _registration_type_from_firebase({})
        assert result == RegistrationType.email


# ---------------------------------------------------------------------------
# _display_name_from_firebase
# ---------------------------------------------------------------------------

class TestDisplayNameFromFirebase:
    def test_uses_name_field(self):
        from apps.accounts.services import _display_name_from_firebase

        result = _display_name_from_firebase({"name": "pytest Alice"}, "a@b.com")
        assert result == "pytest Alice"

    def test_falls_back_to_email_prefix(self):
        from apps.accounts.services import _display_name_from_firebase

        result = _display_name_from_firebase({}, "pytest.user@example.com")
        assert result == "pytest.user"

    def test_empty_name_falls_back_to_email(self):
        from apps.accounts.services import _display_name_from_firebase

        result = _display_name_from_firebase({"name": "  "}, "pytest2@example.com")
        assert result == "pytest2"
>>>>>>> 5038703 (Test cases)
