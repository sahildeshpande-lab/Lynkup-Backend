from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import select

from apps.accounts.db_models import TransactionalEmailLog, User
from apps.administration.db_models.template_db_model import Template
from apps.administration.initial_templates import INITIAL_TEMPLATES
from apps.profiles.db_models import Profile
from apps.profiles.db_models.university_db_model import University
from apps.profiles.services import graduation_email_service as graduation_module
from apps.profiles.services.graduation_email_service import process_graduation_completion_emails
from apps.profiles.services.response_service import alumni_status
from common.enums import OnboardingStatus, UserStatus
from core.email_service import GRADUATION_COMPLETION_PURPOSE, process_pending_emails

_SCHEMA_SQL = """
CREATE TABLE users (
    id CHAR(36) PRIMARY KEY,
    firebase_uid VARCHAR(128),
    email VARCHAR(320) NOT NULL,
    password_hash VARCHAR(255),
    email_otp VARCHAR(16),
    email_otp_created_at TIMESTAMP,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    email_verified_at TIMESTAMP,
    has_changed_email_after_graduation BOOLEAN NOT NULL DEFAULT 0,
    registration_type VARCHAR(20) NOT NULL DEFAULT 'email',
    onboarding_status VARCHAR(32) NOT NULL DEFAULT 'not_started',
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    deleted_at TIMESTAMP,
    purge_after TIMESTAMP,
    is_deleted BOOLEAN NOT NULL DEFAULT 0,
    last_login_at TIMESTAMP,
    referred_by_user_id CHAR(36)
);

CREATE TABLE profiles (
    id CHAR(36) PRIMARY KEY,
    user_id CHAR(36) NOT NULL UNIQUE,
    first_name VARCHAR(64),
    last_name VARCHAR(64),
    bio VARCHAR(500),
    university_id CHAR(36),
    profile_interests_id JSON,
    major VARCHAR(255),
    minor VARCHAR(255),
    major_id INTEGER,
    minor_id INTEGER,
    edu_level VARCHAR(32),
    graduation_date DATE,
    is_alumni BOOLEAN,
    country_id CHAR(36),
    location_text VARCHAR(255),
    profile_visibility VARCHAR(32) NOT NULL DEFAULT 'public',
    online_presence_visible BOOLEAN NOT NULL DEFAULT 1,
    completeness_score INTEGER NOT NULL DEFAULT 0,
    posts_count INTEGER NOT NULL DEFAULT 0,
    followers_count INTEGER NOT NULL DEFAULT 0,
    following_count INTEGER NOT NULL DEFAULT 0,
    completeness_rubric_version VARCHAR(32) NOT NULL DEFAULT 'v1',
    profile_photo_url VARCHAR(2048),
    banner_photo_url VARCHAR(2048),
    extracted_keywords JSON,
    keywords_updated_at TIMESTAMP,
    learning_recommendations JSON,
    recommendations_updated_at TIMESTAMP,
    learning_spotlight JSON,
    learning_spotlight_updated_at TIMESTAMP,
    connection_reminder_sent_at TIMESTAMP,
    graduation_completion_email_sent_at TIMESTAMP,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE universities (
    id CHAR(36) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(255) NOT NULL UNIQUE,
    country_id CHAR(36) NOT NULL,
    website VARCHAR(2048),
    major JSON,
    minor JSON,
    academic_program JSON,
    is_active BOOLEAN NOT NULL DEFAULT 1,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE transactional_email_log (
    id CHAR(36) PRIMARY KEY,
    "to" VARCHAR(320) NOT NULL,
    "from" VARCHAR(320) NOT NULL,
    body TEXT NOT NULL,
    purpose VARCHAR(128) NOT NULL,
    subject VARCHAR(256) NOT NULL DEFAULT '',
    is_sent BOOLEAN NOT NULL DEFAULT 0,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    lease_owner VARCHAR(255),
    lease_expires_at TIMESTAMP,
    attachment VARCHAR(1024),
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE templates (
    id CHAR(36) PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    subject VARCHAR(255) NOT NULL,
    body_html TEXT NOT NULL,
    updated_by CHAR(36),
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
"""


@pytest_asyncio.fixture
async def graduation_db(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        for stmt in _SCHEMA_SQL.split(";"):
            sql = stmt.strip()
            if sql:
                await conn.execute(text(sql))

    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    monkeypatch.setattr(graduation_module, "async_session_factory", session_factory)
    monkeypatch.setattr(
        "core.database.session.async_session_factory",
        session_factory,
    )
    monkeypatch.setenv("SENDGRID_API_KEY", "mock_sendgrid_key")
    monkeypatch.setenv("SENDGRID_FROM_EMAIL", "noreply@example.com")

    async with session_factory() as session:
        await _seed_graduation_template(session)

    yield session_factory
    await engine.dispose()


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _seed_graduation_template(session) -> None:
    tpl_data = next(
        template for template in INITIAL_TEMPLATES if template["name"] == "graduation_email"
    )
    session.add(
        Template(
            name=tpl_data["name"],
            subject=tpl_data["subject"],
            body_html=tpl_data["body_html"],
            status="active",
        )
    )
    await session.commit()


async def _create_graduate(
    session,
    *,
    email: str,
    graduation_date: date | None,
    sent_at: datetime | None = None,
    university_name: str = "Test University",
) -> tuple[User, Profile]:
    now = _now()
    user_id = uuid.uuid4()
    profile_id = uuid.uuid4()
    university_id = uuid.uuid4()

    user = User(
        id=user_id,
        email=email,
        firebase_uid=f"uid-{user_id}",
        status="active",
        created_at=now,
        updated_at=now,
    )
    university = University(
        id=university_id,
        name=university_name,
        slug=f"uni-{university_id.hex[:8]}",
        country_id=uuid.uuid4(),
        updated_at=now,
    )
    profile = Profile(
        id=profile_id,
        user_id=user_id,
        first_name="Alex",
        last_name="Graduate",
        university_id=university_id,
        graduation_date=graduation_date,
        is_alumni=alumni_status(graduation_date),
        graduation_completion_email_sent_at=sent_at,
        updated_at=now,
    )
    session.add(user)
    session.add(university)
    session.add(profile)
    await session.commit()
    return user, profile


@pytest.mark.asyncio
async def test_graduation_producer_skips_null_graduation_date(graduation_db) -> None:
    async with graduation_db() as session:
        await _create_graduate(session, email="null@example.com", graduation_date=None)

    queued = await process_graduation_completion_emails()
    assert queued == 0


@pytest.mark.asyncio
async def test_graduation_producer_skips_today_and_future(graduation_db) -> None:
    today = _now().date()
    async with graduation_db() as session:
        await _create_graduate(session, email="today@example.com", graduation_date=today)
        await _create_graduate(
            session,
            email="future@example.com",
            graduation_date=today + timedelta(days=7),
        )

    queued = await process_graduation_completion_emails()
    assert queued == 0


@pytest.mark.asyncio
async def test_graduation_producer_queues_for_yesterday(graduation_db, monkeypatch) -> None:
    yesterday = _now().date() - timedelta(days=1)
    async with graduation_db() as session:
        user, _profile = await _create_graduate(
            session,
            email="graduated@example.com",
            graduation_date=yesterday,
        )

    monkeypatch.setattr(
        "core.email_service._actually_send_email_via_sendgrid",
        _mock_send_success,
    )

    queued = await process_graduation_completion_emails()
    assert queued == 1

    async with graduation_db() as session:
        logs = (await session.execute(select(TransactionalEmailLog))).scalars().all()
        assert len(logs) == 1
        assert logs[0].to_email == "graduated@example.com"
        assert logs[0].purpose == GRADUATION_COMPLETION_PURPOSE
        assert logs[0].is_send is False
        assert "Congratulations on your graduation!" in logs[0].content
        assert "Test University" in logs[0].content

        refreshed = (
            await session.execute(select(Profile).where(Profile.user_id == user.id))
        ).scalar_one()
        assert refreshed.graduation_completion_email_sent_at is not None
        assert refreshed.is_alumni is True


@pytest.mark.asyncio
async def test_graduation_producer_marks_completed_graduates_as_alumni_without_email(
    graduation_db,
) -> None:
    yesterday = _now().date() - timedelta(days=1)
    async with graduation_db() as session:
        user, _profile = await _create_graduate(
            session,
            email="already-emailed@example.com",
            graduation_date=yesterday,
            sent_at=_now(),
        )

    queued = await process_graduation_completion_emails()
    assert queued == 0

    async with graduation_db() as session:
        refreshed = (
            await session.execute(select(Profile).where(Profile.user_id == user.id))
        ).scalar_one()
        assert refreshed.is_alumni is True


@pytest.mark.asyncio
async def test_graduation_producer_leaves_incomplete_graduates_as_non_alumni(
    graduation_db,
) -> None:
    today = _now().date()
    async with graduation_db() as session:
        user, _profile = await _create_graduate(
            session,
            email="still-student@example.com",
            graduation_date=today + timedelta(days=30),
        )

    await process_graduation_completion_emails()

    async with graduation_db() as session:
        refreshed = (
            await session.execute(select(Profile).where(Profile.user_id == user.id))
        ).scalar_one()
        assert refreshed.is_alumni is False


async def _mock_send_success(*args, **kwargs):
    return True


@pytest.mark.asyncio
async def test_graduation_producer_does_not_queue_twice(graduation_db, monkeypatch) -> None:
    yesterday = _now().date() - timedelta(days=1)
    async with graduation_db() as session:
        await _create_graduate(
            session,
            email="once@example.com",
            graduation_date=yesterday,
        )

    monkeypatch.setattr(
        "core.email_service._actually_send_email_via_sendgrid",
        _mock_send_success,
    )

    first = await process_graduation_completion_emails()
    second = await process_graduation_completion_emails()
    assert first == 1
    assert second == 0

    async with graduation_db() as session:
        logs = (await session.execute(select(TransactionalEmailLog))).scalars().all()
        assert len(logs) == 1


@pytest.mark.asyncio
async def test_graduation_email_queue_delivered_by_existing_cron(graduation_db, monkeypatch) -> None:
    yesterday = _now().date() - timedelta(days=1)
    async with graduation_db() as session:
        await _create_graduate(
            session,
            email="cron@example.com",
            graduation_date=yesterday,
        )

    monkeypatch.setattr(
        "core.email_service._actually_send_email_via_sendgrid",
        _mock_send_success,
    )

    await process_graduation_completion_emails()

    processed = await process_pending_emails(limit=10)
    assert processed == 1

    async with graduation_db() as session:
        log = (await session.execute(select(TransactionalEmailLog))).scalar_one()
        assert log.is_send is True


@pytest.mark.asyncio
async def test_graduation_email_tick_delivers_only_graduation_purpose(
    graduation_db, monkeypatch
) -> None:
    from apps.profiles.services.graduation_email_service import run_graduation_email_tick

    yesterday = _now().date() - timedelta(days=1)
    async with graduation_db() as session:
        await _create_graduate(
            session,
            email="grad-tick@example.com",
            graduation_date=yesterday,
        )
        session.add(
            TransactionalEmailLog(
                to_email="other@example.com",
                from_email="noreply@kampulynk.com",
                subject="Other",
                content="<p>Other</p>",
                purpose="Connection Reminder",
                is_send=False,
            )
        )
        await session.commit()

    monkeypatch.setattr(
        "core.email_service._actually_send_email_via_sendgrid",
        _mock_send_success,
    )

    result = await run_graduation_email_tick()
    assert result["queued"] == 1
    assert result["delivered"] == 1

    async with graduation_db() as session:
        logs = (await session.execute(select(TransactionalEmailLog))).scalars().all()
        by_purpose = {log.purpose: log for log in logs}
        assert by_purpose[GRADUATION_COMPLETION_PURPOSE].is_send is True
        assert by_purpose["Connection Reminder"].is_send is False


@pytest.mark.asyncio
async def test_manual_graduation_runcron_queues_celery_worker(monkeypatch) -> None:
    from unittest.mock import AsyncMock, patch

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from apps.administration.routes import router as admin_router
    from core.celery_worker.config import CeleryTaskQueue
    from core.database.session import get_session
    from core.security.auth import get_current_admin

    admin_id = uuid4()
    mock_admin = User(
        id=admin_id,
        email="admin@example.com",
        role="superadmin",
        status=UserStatus.active,
        onboarding_status=OnboardingStatus.completed,
    )
    app = FastAPI()
    app.include_router(admin_router, prefix="/api/v1")

    async def _override_admin():
        return mock_admin

    async def _override_db():
        yield AsyncMock()

    app.dependency_overrides[get_current_admin] = _override_admin
    app.dependency_overrides[get_session] = _override_db

    with (
        patch(
            "core.jobs.publishing.publish_admin_task",
            new=AsyncMock(return_value="graduation-task"),
        ) as publish,
        patch(
            "apps.administration.services.admin_activity_log_service.create_admin_activity_log",
            new=AsyncMock(),
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/admin/graduation/runcron")

    assert resp.status_code == 202
    body = resp.json()
    assert body["data"] == {"task_id": "graduation-task", "status": "queued"}
    publish.assert_awaited_once_with(
        "kampulynk.graduation.tick",
        CeleryTaskQueue.TRANSACTIONAL_QUEUE,
    )


@pytest.mark.asyncio
async def test_transactional_cron_runs_graduation_producer(monkeypatch) -> None:
    from core.email_service import process_transactional_emails

    called = {"grad": False, "reminders": False, "pending": False}

    async def _grad(limit=None, *, session_factory=None):
        called["grad"] = True
        return 0

    async def _reminders(limit=None, *, session_factory=None):
        called["reminders"] = True
        return 0

    async def _pending(limit=10, *, session_factory=None, lease_owner=None, purpose=None):
        called["pending"] = True
        return 0

    monkeypatch.setattr(
        "apps.profiles.services.graduation_email_service.process_graduation_completion_emails",
        _grad,
    )
    monkeypatch.setattr(
        "apps.connections.services.connection_reminder_service.process_connection_reminders",
        _reminders,
    )
    monkeypatch.setattr("core.email_service.process_pending_emails", _pending)

    await process_transactional_emails()
    assert called == {"grad": True, "reminders": True, "pending": True}


@pytest.mark.asyncio
async def test_verify_otp_keeps_has_changed_email_after_graduation_true(monkeypatch) -> None:
    from apps.accounts.services.auth_service import verify_otp
    from apps.accounts.schemas import OtpVerifyRequest

    user = User(
        id=uuid4(),
        email="changed@example.com",
        firebase_uid="firebase-uid",
        status=UserStatus.active,
        email_verified_at=None,
        has_changed_email_after_graduation=True,
        email_otp="1234",
        email_otp_created_at=_now(),
        onboarding_status=OnboardingStatus.completed,
    )

    def _execute_result(*, user_value=None, profile_value=None, one=False):
        mock = MagicMock()
        if one:
            mock.scalar_one.return_value = user_value
        else:
            mock.scalar_one_or_none.return_value = profile_value if profile_value is not None else user_value
        return mock

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(
        side_effect=[
            _execute_result(user_value=user),
            _execute_result(profile_value=Profile(user_id=user.id, first_name="Changed")),
            _execute_result(user_value=user, one=True),
        ]
    )
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    monkeypatch.setattr(
        "apps.accounts.services.auth_service.otp_matches",
        lambda _user, _otp: True,
    )
    monkeypatch.setattr(
        "apps.accounts.services.auth_service.mark_pending_active_device_verified",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "apps.accounts.services.auth_service._issue_auth_session",
        AsyncMock(return_value={"user": {"email": user.email}}),
    )
    monkeypatch.setattr(
        "apps.notifications.services.topic_service.TopicService.refresh_user_topic_subscriptions",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "apps.chat.service.sync_stream_user_on_auth",
        AsyncMock(),
    )

    payload = OtpVerifyRequest(
        email=user.email,
        otp="1234",
        firebaseId=user.firebase_uid,
    )
    result = await verify_otp(payload, {"uid": user.firebase_uid}, mock_db)

    assert result.status is True
    assert user.email_verified_at is not None
    assert user.has_changed_email_after_graduation is True
