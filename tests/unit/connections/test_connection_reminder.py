"""Tests for pending connection-request reminder producer.

Uses an isolated in-memory SQLite schema (minimal tables) so tests do not
depend on ALTER privileges on the shared development Postgres instance.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.accounts.db_models import User
from apps.connections.db_models import ConnectionRequest
from apps.connections.services import connection_reminder_service as reminder_module
from apps.connections.services.connection_reminder_service import (
    ConnectionReminderService,
    process_connection_reminders,
)
from apps.profiles.db_models import Profile


def _now() -> datetime:
    return datetime.now(timezone.utc)


_SCHEMA_SQL = """
CREATE TABLE users (
    id CHAR(36) PRIMARY KEY,
    firebase_uid VARCHAR(128),
    email VARCHAR(320) NOT NULL UNIQUE,
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

CREATE TABLE connection_requests (
    id CHAR(36) PRIMARY KEY,
    sender_user_id CHAR(36) NOT NULL,
    receiver_user_id CHAR(36) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE notification_types (
    id CHAR(36) PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    description TEXT,
    is_active BOOLEAN NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE notifications (
    id CHAR(36) PRIMARY KEY,
    recipient_user_id CHAR(36) NOT NULL,
    notification_type_id CHAR(36) NOT NULL,
    campaign_id CHAR(36),
    title VARCHAR(255) NOT NULL,
    body TEXT NOT NULL,
    deep_link_payload JSON,
    is_read BOOLEAN NOT NULL DEFAULT 0,
    read_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE notification_preferences (
    id CHAR(36) PRIMARY KEY,
    user_id CHAR(36) NOT NULL UNIQUE,
    push_enabled BOOLEAN NOT NULL DEFAULT 1,
    in_app_enabled BOOLEAN NOT NULL DEFAULT 1,
    category_preferences JSON NOT NULL DEFAULT '{}',
    email_preferences JSON NOT NULL DEFAULT '{"bulk_email": true}',
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
"""


@pytest_asyncio.fixture
async def reminder_db(monkeypatch):
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
    monkeypatch.setattr(reminder_module, "async_session_factory", session_factory)
    monkeypatch.setattr(
        "core.database.session.async_session_factory",
        session_factory,
    )

    yield session_factory
    await engine.dispose()


async def _create_user(
    session,
    *,
    email: str,
    first_name: str,
    last_name: str,
    major: str | None = None,
    reminder_sent_at: datetime | None = None,
) -> tuple[User, Profile]:
    now = _now()
    user = User(
        email=email,
        firebase_uid=f"uid-{uuid.uuid4()}",
        status="active",
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    await session.flush()
    profile = Profile(
        user_id=user.id,
        first_name=first_name,
        last_name=last_name,
        major=major,
        connection_reminder_sent_at=reminder_sent_at,
        updated_at=now,
    )
    session.add(profile)
    await session.flush()
    return user, profile


async def _add_pending(
    session,
    *,
    sender_id,
    receiver_id,
    status: str = "pending",
) -> ConnectionRequest:
    now = _now()
    req = ConnectionRequest(
        sender_user_id=sender_id,
        receiver_user_id=receiver_id,
        status=status,
        created_at=now,
        updated_at=now,
    )
    session.add(req)
    await session.flush()
    return req


@pytest.mark.asyncio
async def test_no_pending_requests_creates_no_reminder(reminder_db):
    async with reminder_db() as session:
        receiver, _ = await _create_user(
            session,
            email="pytest_crem_recv0@example.com",
            first_name="Alex",
            last_name="Receiver",
        )
        await session.commit()

    with patch.object(reminder_module, "notify_connection_reminder", AsyncMock()) as notify_mock:
        dispatched = await process_connection_reminders()
        assert dispatched == 0
        notify_mock.assert_not_awaited()

    async with reminder_db() as session:
        profile = (
            await session.execute(select(Profile).where(Profile.user_id == receiver.id))
        ).scalar_one()
        assert profile.connection_reminder_sent_at is None


@pytest.mark.asyncio
async def test_first_reminder_dispatches_when_sent_at_null(reminder_db):
    async with reminder_db() as session:
        receiver, _ = await _create_user(
            session,
            email="pytest_crem_recv1@example.com",
            first_name="Alex",
            last_name="Receiver",
        )
        sender, _ = await _create_user(
            session,
            email="pytest_crem_send1@example.com",
            first_name="Blake",
            last_name="Sender",
            major="Computer Science",
        )
        await _add_pending(session, sender_id=sender.id, receiver_id=receiver.id)
        await session.commit()

    with patch.object(
        reminder_module, "notify_connection_reminder", AsyncMock(return_value=object())
    ) as notify_mock:
        dispatched = await process_connection_reminders()
        assert dispatched == 1
        notify_mock.assert_awaited_once()
        kwargs = notify_mock.await_args.kwargs
        assert kwargs["recipient_user_id"] == receiver.id
        assert kwargs["pending_count"] == 1
        assert kwargs["sender_name"] == "Blake"
        assert kwargs["sender_user_id"] == sender.id

    async with reminder_db() as session:
        profile = (
            await session.execute(select(Profile).where(Profile.user_id == receiver.id))
        ).scalar_one()
        assert profile.connection_reminder_sent_at is not None


@pytest.mark.asyncio
async def test_reminder_before_7_days_is_skipped(reminder_db):
    recent = _now() - timedelta(days=3)
    async with reminder_db() as session:
        receiver, _ = await _create_user(
            session,
            email="pytest_crem_recv2@example.com",
            first_name="Alex",
            last_name="Receiver",
            reminder_sent_at=recent,
        )
        sender, _ = await _create_user(
            session,
            email="pytest_crem_send2@example.com",
            first_name="Casey",
            last_name="Sender",
        )
        await _add_pending(session, sender_id=sender.id, receiver_id=receiver.id)
        await session.commit()

    with patch.object(reminder_module, "notify_connection_reminder", AsyncMock()) as notify_mock:
        dispatched = await process_connection_reminders()
        assert dispatched == 0
        notify_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_reminder_after_7_days_dispatches(reminder_db):
    old = _now() - timedelta(days=8)
    async with reminder_db() as session:
        receiver, _ = await _create_user(
            session,
            email="pytest_crem_recv3@example.com",
            first_name="Alex",
            last_name="Receiver",
            reminder_sent_at=old,
        )
        sender, _ = await _create_user(
            session,
            email="pytest_crem_send3@example.com",
            first_name="Dana",
            last_name="Sender",
        )
        await _add_pending(session, sender_id=sender.id, receiver_id=receiver.id)
        await session.commit()

    with patch.object(
        reminder_module, "notify_connection_reminder", AsyncMock(return_value=object())
    ) as notify_mock:
        dispatched = await process_connection_reminders()
        assert dispatched == 1
        notify_mock.assert_awaited_once()
        kwargs = notify_mock.await_args.kwargs
        assert kwargs["recipient_user_id"] == receiver.id
        assert kwargs["pending_count"] == 1
        assert kwargs["sender_name"] == "Dana"


@pytest.mark.asyncio
async def test_multiple_pending_requests_produce_one_reminder_with_count(reminder_db):
    async with reminder_db() as session:
        receiver, _ = await _create_user(
            session,
            email="pytest_crem_recv4@example.com",
            first_name="Alex",
            last_name="Receiver",
        )
        sender_b, _ = await _create_user(
            session,
            email="pytest_crem_send4b@example.com",
            first_name="Blake",
            last_name="Beta",
        )
        sender_c, _ = await _create_user(
            session,
            email="pytest_crem_send4c@example.com",
            first_name="Casey",
            last_name="Charlie",
        )
        await _add_pending(session, sender_id=sender_b.id, receiver_id=receiver.id)
        await _add_pending(session, sender_id=sender_c.id, receiver_id=receiver.id)
        await session.commit()

    with patch.object(
        reminder_module, "notify_connection_reminder", AsyncMock(return_value=object())
    ) as notify_mock:
        dispatched = await process_connection_reminders()
        assert dispatched == 1
        notify_mock.assert_awaited_once()
        kwargs = notify_mock.await_args.kwargs
        assert kwargs["recipient_user_id"] == receiver.id
        assert kwargs["pending_count"] == 2
        assert kwargs["sender_name"] is None
        assert kwargs["sender_user_id"] is None


@pytest.mark.asyncio
async def test_accepted_request_excluded_from_reminder(reminder_db):
    async with reminder_db() as session:
        receiver, _ = await _create_user(
            session,
            email="pytest_crem_recv5@example.com",
            first_name="Alex",
            last_name="Receiver",
        )
        sender_b, _ = await _create_user(
            session,
            email="pytest_crem_send5b@example.com",
            first_name="Blake",
            last_name="Accepted",
        )
        sender_c, _ = await _create_user(
            session,
            email="pytest_crem_send5c@example.com",
            first_name="Casey",
            last_name="Pending",
        )
        await _add_pending(
            session, sender_id=sender_b.id, receiver_id=receiver.id, status="accepted"
        )
        await _add_pending(session, sender_id=sender_c.id, receiver_id=receiver.id)
        await session.commit()

    with patch.object(
        reminder_module, "notify_connection_reminder", AsyncMock(return_value=object())
    ) as notify_mock:
        dispatched = await process_connection_reminders()
        assert dispatched == 1
        notify_mock.assert_awaited_once()
        kwargs = notify_mock.await_args.kwargs
        assert kwargs["pending_count"] == 1
        assert kwargs["sender_name"] == "Casey"


@pytest.mark.asyncio
async def test_declined_request_excluded_from_reminder(reminder_db):
    async with reminder_db() as session:
        receiver, _ = await _create_user(
            session,
            email="pytest_crem_recv6@example.com",
            first_name="Alex",
            last_name="Receiver",
        )
        sender_b, _ = await _create_user(
            session,
            email="pytest_crem_send6b@example.com",
            first_name="Blake",
            last_name="Declined",
        )
        sender_c, _ = await _create_user(
            session,
            email="pytest_crem_send6c@example.com",
            first_name="Casey",
            last_name="StillPending",
        )
        await _add_pending(
            session, sender_id=sender_b.id, receiver_id=receiver.id, status="declined"
        )
        await _add_pending(session, sender_id=sender_c.id, receiver_id=receiver.id)
        await session.commit()

    with patch.object(
        reminder_module, "notify_connection_reminder", AsyncMock(return_value=object())
    ) as notify_mock:
        dispatched = await process_connection_reminders()
        assert dispatched == 1
        notify_mock.assert_awaited_once()
        kwargs = notify_mock.await_args.kwargs
        assert kwargs["pending_count"] == 1
        assert kwargs["sender_name"] == "Casey"


@pytest.mark.asyncio
async def test_all_requests_resolved_creates_no_reminder(reminder_db):
    async with reminder_db() as session:
        receiver, _ = await _create_user(
            session,
            email="pytest_crem_recv7@example.com",
            first_name="Alex",
            last_name="Receiver",
        )
        sender_b, _ = await _create_user(
            session,
            email="pytest_crem_send7b@example.com",
            first_name="Blake",
            last_name="Done",
        )
        sender_c, _ = await _create_user(
            session,
            email="pytest_crem_send7c@example.com",
            first_name="Casey",
            last_name="AlsoDone",
        )
        await _add_pending(
            session, sender_id=sender_b.id, receiver_id=receiver.id, status="accepted"
        )
        await _add_pending(
            session, sender_id=sender_c.id, receiver_id=receiver.id, status="declined"
        )
        await session.commit()

    with patch.object(reminder_module, "notify_connection_reminder", AsyncMock()) as notify_mock:
        dispatched = await process_connection_reminders()
        assert dispatched == 0
        notify_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_producer_does_not_duplicate_for_same_user(reminder_db):
    async with reminder_db() as session:
        receiver, _ = await _create_user(
            session,
            email="pytest_crem_recv8@example.com",
            first_name="Alex",
            last_name="Receiver",
        )
        sender, _ = await _create_user(
            session,
            email="pytest_crem_send8@example.com",
            first_name="Blake",
            last_name="Sender",
        )
        await _add_pending(session, sender_id=sender.id, receiver_id=receiver.id)
        await session.commit()
        receiver_id = receiver.id

    service = ConnectionReminderService()
    cutoff = service._cutoff()

    with patch.object(
        reminder_module, "notify_connection_reminder", AsyncMock(return_value=object())
    ) as notify_mock:
        assert await service._send_reminder_for_receiver(receiver_id, cutoff=cutoff) is True
        assert await service._send_reminder_for_receiver(receiver_id, cutoff=cutoff) is False
        assert notify_mock.await_count == 1

        # Concurrent attempt after stamp must also be a no-op.
        results = await asyncio.gather(
            service._send_reminder_for_receiver(receiver_id, cutoff=cutoff),
            service._send_reminder_for_receiver(receiver_id, cutoff=cutoff),
        )
        assert all(ok is False for ok in results)


@pytest.mark.asyncio
async def test_weekly_preference_off_skips_notify_but_stamps_cadence(reminder_db):
    async with reminder_db() as session:
        receiver, _ = await _create_user(
            session,
            email="pytest_crem_recv_weekly_off@example.com",
            first_name="Alex",
            last_name="Receiver",
        )
        sender, _ = await _create_user(
            session,
            email="pytest_crem_send_weekly_off@example.com",
            first_name="Blake",
            last_name="Sender",
        )
        await _add_pending(session, sender_id=sender.id, receiver_id=receiver.id)
        from apps.notifications.db_models import NotificationPreference

        now = _now()
        session.add(
            NotificationPreference(
                user_id=receiver.id,
                push_enabled=True,
                in_app_enabled=True,
                category_preferences={"weekly_lynkup_request_reminder": False},
                email_preferences={"bulk_email": True},
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()
        receiver_id = receiver.id

    with patch.object(reminder_module, "notify_connection_reminder", AsyncMock()) as notify_mock:
        dispatched = await process_connection_reminders()
        assert dispatched == 1
        notify_mock.assert_not_awaited()

    async with reminder_db() as session:
        profile = (
            await session.execute(select(Profile).where(Profile.user_id == receiver_id))
        ).scalar_one()
        assert profile.connection_reminder_sent_at is not None


@pytest.mark.asyncio
async def test_notification_failure_does_not_prevent_future_retry(reminder_db):
    async with reminder_db() as session:
        receiver, _ = await _create_user(
            session,
            email="pytest_crem_recv9@example.com",
            first_name="Alex",
            last_name="Receiver",
        )
        sender, _ = await _create_user(
            session,
            email="pytest_crem_send9@example.com",
            first_name="Blake",
            last_name="Sender",
        )
        await _add_pending(session, sender_id=sender.id, receiver_id=receiver.id)
        await session.commit()
        receiver_id = receiver.id

    with patch.object(
        reminder_module,
        "notify_connection_reminder",
        new=AsyncMock(side_effect=RuntimeError("notification failed")),
    ):
        dispatched = await process_connection_reminders()
        assert dispatched == 0

    async with reminder_db() as session:
        profile = (
            await session.execute(select(Profile).where(Profile.user_id == receiver_id))
        ).scalar_one()
        assert profile.connection_reminder_sent_at is None


@pytest.mark.asyncio
async def test_different_users_each_get_own_reminder(reminder_db):
    async with reminder_db() as session:
        receiver_a, _ = await _create_user(
            session,
            email="pytest_crem_recva@example.com",
            first_name="Alex",
            last_name="A",
        )
        receiver_b, _ = await _create_user(
            session,
            email="pytest_crem_recvb@example.com",
            first_name="Bailey",
            last_name="B",
        )
        sender_a, _ = await _create_user(
            session,
            email="pytest_crem_senda@example.com",
            first_name="Sam",
            last_name="ForA",
        )
        sender_b, _ = await _create_user(
            session,
            email="pytest_crem_sendb@example.com",
            first_name="Sky",
            last_name="ForB",
        )
        await _add_pending(session, sender_id=sender_a.id, receiver_id=receiver_a.id)
        await _add_pending(session, sender_id=sender_b.id, receiver_id=receiver_b.id)
        await session.commit()

    with patch.object(
        reminder_module, "notify_connection_reminder", AsyncMock(return_value=object())
    ) as notify_mock:
        dispatched = await process_connection_reminders()
        assert dispatched == 2
        assert notify_mock.await_count == 2
