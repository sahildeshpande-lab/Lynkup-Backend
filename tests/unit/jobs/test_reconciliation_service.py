from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from core.celery_worker.config import CeleryTaskQueue

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool, StaticPool

from apps.accounts.db_models import TransactionalEmailLog
from apps.bulk_send.enums import EmailCampaignStatus, EmailDeliveryStatus
from apps.bulk_send.models import EmailCampaign, EmailDelivery
from apps.export.enums import DataExportStatus
from apps.export.models import DataExportRequest
from common.enums import NotificationCampaignStatus, NotificationCampaignType
from core.jobs.reconciliation_service import (
    BULK_EMAIL_TICK,
    DELETION_TICK,
    EXPORT_PROCESS_TASK,
    MODERATION_TICK,
    NOTIFICATION_CAMPAIGN_DISPATCH_TASK,
    SPOTLIGHT_TICK,
    TRANSACTIONAL_EMAIL_TICK,
    _lock_ids,
    default_publisher,
    recoverable_bulk_deliveries_stmt,
    recoverable_deletion_users_stmt,
    recoverable_exports_stmt,
    recoverable_moderation_comments_stmt,
    recoverable_moderation_posts_stmt,
    recoverable_notification_campaigns_stmt,
    recoverable_transactional_emails_stmt,
    run_reconciliation_batch,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _compile(stmt) -> str:
    return str(
        stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    ).lower()


class RecordingPublisher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, list[str]]] = []

    def __call__(self, name: str, *, queue: str, args=None) -> None:
        self.calls.append((name, queue, list(args or [])))


class FailFirstPublisher(RecordingPublisher):
    def __init__(self) -> None:
        super().__init__()
        self._failed = False

    def __call__(self, name: str, *, queue: str, args=None) -> None:
        super().__call__(name, queue=queue, args=args)
        if not self._failed:
            self._failed = True
            raise ConnectionError("broker unavailable")


async def _noop_family(*args, **kwargs) -> None:
    return None


_RECOVERY_FAMILIES = (
    "_recover_exports",
    "_recover_transactional_email",
    "_recover_bulk_email",
    "_recover_notification_campaigns",
    "_recover_moderation",
    "_recover_deletion",
    "_recover_spotlight",
)


def _isolate_to(monkeypatch, *keep: str) -> None:
    """No-op every reconciliation family except the ones under test."""
    import core.jobs.reconciliation_service as service

    for name in _RECOVERY_FAMILIES:
        if name not in keep:
            monkeypatch.setattr(service, name, _noop_family)


@pytest.fixture
def export_only(monkeypatch):
    _isolate_to(monkeypatch, "_recover_exports")


@pytest_asyncio.fixture
async def export_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(DataExportRequest.__table__.create)
    factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    try:
        yield factory
    finally:
        await engine.dispose()


async def _insert_export(
    factory,
    *,
    status: DataExportStatus = DataExportStatus.queued,
    lease_owner: str | None = None,
    lease_expires_at: datetime | None = None,
) -> DataExportRequest:
    row = DataExportRequest(
        id=uuid4(),
        user_id=uuid4(),
        status=status,
        lease_owner=lease_owner,
        lease_expires_at=lease_expires_at,
    )
    async with factory() as session:
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row


async def _reload_export(factory, export_id) -> DataExportRequest:
    from sqlmodel import select

    async with factory() as session:
        return (
            await session.execute(
                select(DataExportRequest).where(DataExportRequest.id == export_id)
            )
        ).scalar_one()


def test_recoverable_exports_sql_uses_skip_locked_and_server_time():
    stmt = recoverable_exports_stmt(25).with_for_update(skip_locked=True)
    sql = _compile(stmt)
    assert "data_export_requests" in sql
    assert "for update" in sql
    assert "skip locked" in sql
    assert "queued" in sql
    assert "processing" in sql
    assert "current_timestamp" in sql or "now()" in sql
    assert "completed" not in sql
    assert "failed" not in sql
    assert "expired" not in sql
    assert "limit 25" in sql


def test_recoverable_family_sql_uses_skip_locked_and_does_not_reopen_terminal_states():
    email_sql = _compile(
        recoverable_transactional_emails_stmt(10).with_for_update(skip_locked=True)
    )
    assert "transactional_email_log" in email_sql
    assert "skip locked" in email_sql
    assert "lease_expires_at" in email_sql
    assert "current_timestamp" in email_sql or "now()" in email_sql

    bulk_sql = _compile(recoverable_bulk_deliveries_stmt(10).with_for_update(skip_locked=True))
    assert "email_deliveries" in bulk_sql
    assert "for update" in bulk_sql
    assert "skip locked" in bulk_sql
    assert "processing" in bulk_sql
    assert "completed" not in bulk_sql.split("where", 1)[-1]

    campaign_sql = _compile(
        recoverable_notification_campaigns_stmt(10).with_for_update(skip_locked=True)
    )
    assert "notification_campaigns" in campaign_sql
    assert "skip locked" in campaign_sql
    assert "draft" in campaign_sql
    assert "is_active" in campaign_sql

    post_sql = _compile(recoverable_moderation_posts_stmt(10).with_for_update(skip_locked=True))
    assert "posts" in post_sql
    assert "skip locked" in post_sql
    assert "moderation_lease_expires_at" in post_sql
    assert "current_timestamp" in post_sql

    comment_sql = _compile(
        recoverable_moderation_comments_stmt(10).with_for_update(skip_locked=True)
    )
    assert "comments" in comment_sql
    assert "skip locked" in comment_sql

    deletion_sql = _compile(recoverable_deletion_users_stmt(1).with_for_update(skip_locked=True))
    assert "users" in deletion_sql
    assert "deleting" in deletion_sql
    assert "purge_after" in deletion_sql
    assert "skip locked" in deletion_sql
    assert "current_timestamp" in deletion_sql


def test_default_publisher_sends_json_task_name_and_queue(monkeypatch):
    seen = {}

    class FakeApp:
        def send_task(self, name, args=None, queue=None):
            seen["name"] = name
            seen["args"] = args
            seen["queue"] = queue

    monkeypatch.setattr("core.celery_worker.celery_app.celery_app", FakeApp())
    default_publisher(EXPORT_PROCESS_TASK, queue=CeleryTaskQueue.EXPORTS_QUEUE.value, args=["export-id"])
    assert seen == {
        "name": EXPORT_PROCESS_TASK,
        "args": ["export-id"],
        "queue": CeleryTaskQueue.EXPORTS_QUEUE.value,
    }


@pytest.mark.asyncio
async def test_disabled_reconciliation_batch_scans_nothing(export_factory):
    publisher = RecordingPublisher()
    await _insert_export(export_factory)
    outcome = await run_reconciliation_batch(
        session_factory=export_factory,
        publisher=publisher,
        enabled=False,
        batch_size=25,
    )
    assert outcome.as_log_dict() == {
        "scanned": 0,
        "claimed": 0,
        "republished": 0,
        "skipped": 0,
        "failed": 0,
        "families": {},
    }
    assert publisher.calls == []


@pytest.mark.asyncio
async def test_bounded_batch_size(export_factory, export_only):
    for _ in range(5):
        await _insert_export(export_factory)
    publisher = RecordingPublisher()
    outcome = await run_reconciliation_batch(
        session_factory=export_factory,
        publisher=publisher,
        batch_size=2,
        enabled=True,
    )
    assert outcome.scanned == 2
    assert outcome.claimed == 2
    assert outcome.republished == 2
    assert len(publisher.calls) == 2
    assert {call[0] for call in publisher.calls} == {EXPORT_PROCESS_TASK}
    assert {call[1] for call in publisher.calls} == {CeleryTaskQueue.EXPORTS_QUEUE.value}
    for _name, _queue, args in publisher.calls:
        assert len(args) == 1
        UUID(args[0])


@pytest.mark.asyncio
async def test_expired_processing_lease_is_claimed(export_factory, export_only):
    expired = await _insert_export(
        export_factory,
        status=DataExportStatus.processing,
        lease_owner="dead-worker",
        lease_expires_at=utc_now() - timedelta(minutes=5),
    )
    publisher = RecordingPublisher()
    outcome = await run_reconciliation_batch(
        session_factory=export_factory,
        publisher=publisher,
        batch_size=10,
        enabled=True,
    )
    assert outcome.claimed == 1
    assert publisher.calls == [
        (EXPORT_PROCESS_TASK, CeleryTaskQueue.EXPORTS_QUEUE.value, [str(expired.id)]),
    ]
    reloaded = await _reload_export(export_factory, expired.id)
    assert reloaded.status == DataExportStatus.processing
    assert reloaded.lease_owner == "dead-worker"


@pytest.mark.asyncio
async def test_non_expired_lease_is_not_reclaimed(export_factory, export_only):
    await _insert_export(
        export_factory,
        status=DataExportStatus.processing,
        lease_owner="live-worker",
        lease_expires_at=utc_now() + timedelta(minutes=20),
    )
    publisher = RecordingPublisher()
    outcome = await run_reconciliation_batch(
        session_factory=export_factory,
        publisher=publisher,
        batch_size=10,
        enabled=True,
    )
    assert outcome.claimed == 0
    assert outcome.republished == 0
    assert publisher.calls == []


@pytest.mark.asyncio
async def test_terminal_exports_are_not_reclaimed(export_factory, export_only):
    await _insert_export(export_factory, status=DataExportStatus.completed)
    await _insert_export(export_factory, status=DataExportStatus.failed)
    await _insert_export(export_factory, status=DataExportStatus.expired)
    publisher = RecordingPublisher()
    outcome = await run_reconciliation_batch(
        session_factory=export_factory,
        publisher=publisher,
        batch_size=10,
        enabled=True,
    )
    assert outcome.claimed == 0
    assert publisher.calls == []


@pytest.mark.asyncio
async def test_duplicate_reconciliation_publication_remains_safe(
    export_factory, export_only
):
    row = await _insert_export(export_factory)
    first = RecordingPublisher()
    second = RecordingPublisher()
    await run_reconciliation_batch(
        session_factory=export_factory,
        publisher=first,
        batch_size=10,
        enabled=True,
    )
    await run_reconciliation_batch(
        session_factory=export_factory,
        publisher=second,
        batch_size=10,
        enabled=True,
    )
    assert first.calls == second.calls == [
        (EXPORT_PROCESS_TASK, CeleryTaskQueue.EXPORTS_QUEUE.value, [str(row.id)]),
    ]
    reloaded = await _reload_export(export_factory, row.id)
    assert reloaded.status == DataExportStatus.queued


@pytest.mark.asyncio
async def test_broker_publish_failure_does_not_lose_recoverable_work(
    export_factory, export_only
):
    row = await _insert_export(export_factory)
    failing = FailFirstPublisher()
    first = await run_reconciliation_batch(
        session_factory=export_factory,
        publisher=failing,
        batch_size=10,
        enabled=True,
    )
    assert first.failed == 1
    assert first.republished == 0
    reloaded = await _reload_export(export_factory, row.id)
    assert reloaded.status == DataExportStatus.queued

    retry = RecordingPublisher()
    second = await run_reconciliation_batch(
        session_factory=export_factory,
        publisher=retry,
        batch_size=10,
        enabled=True,
    )
    assert second.republished == 1
    assert retry.calls == [(EXPORT_PROCESS_TASK, CeleryTaskQueue.EXPORTS_QUEUE.value, [str(row.id)])]


@pytest.mark.asyncio
async def test_unexpected_family_failure_propagates(export_factory, monkeypatch):
    import core.jobs.reconciliation_service as service

    async def boom(*args, **kwargs):
        raise RuntimeError("export scan failed")

    _isolate_to(monkeypatch)  # noop every family
    monkeypatch.setattr(service, "_recover_exports", boom)

    with pytest.raises(RuntimeError, match="export scan failed"):
        await run_reconciliation_batch(
            session_factory=export_factory,
            publisher=RecordingPublisher(),
            enabled=True,
        )


@pytest_asyncio.fixture
async def email_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(TransactionalEmailLog.__table__.create)
    factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_expired_transactional_lease_republishes_tick(email_factory, monkeypatch):
    _isolate_to(monkeypatch, "_recover_transactional_email")

    log = TransactionalEmailLog(
        to_email="stuck@example.com",
        from_email="noreply@example.com",
        content="body",
        purpose="test",
        subject="subject",
        is_send=False,
        lease_owner="dead-worker",
        lease_expires_at=utc_now() - timedelta(minutes=10),
    )
    async with email_factory() as session:
        session.add(log)
        await session.commit()
        await session.refresh(log)

    publisher = RecordingPublisher()
    outcome = await run_reconciliation_batch(
        session_factory=email_factory,
        publisher=publisher,
        batch_size=10,
        enabled=True,
    )
    assert outcome.claimed == 1
    assert publisher.calls == [(TRANSACTIONAL_EMAIL_TICK, CeleryTaskQueue.TRANSACTIONAL_QUEUE.value, [])]
    async with email_factory() as session:
        reloaded = await session.get(TransactionalEmailLog, log.id)
        assert reloaded is not None
        assert reloaded.is_send is False
        assert reloaded.lease_owner == "dead-worker"


@pytest.mark.asyncio
async def test_sent_transactional_email_is_not_reclaimed(email_factory, monkeypatch):
    _isolate_to(monkeypatch, "_recover_transactional_email")

    log = TransactionalEmailLog(
        to_email="done@example.com",
        from_email="noreply@example.com",
        content="body",
        purpose="test",
        subject="subject",
        is_send=True,
        lease_owner="worker",
        lease_expires_at=utc_now() - timedelta(minutes=10),
    )
    async with email_factory() as session:
        session.add(log)
        await session.commit()

    publisher = RecordingPublisher()
    outcome = await run_reconciliation_batch(
        session_factory=email_factory,
        publisher=publisher,
        enabled=True,
    )
    assert outcome.claimed == 0
    assert publisher.calls == []


@pytest.mark.asyncio
async def test_active_transactional_lease_is_not_reclaimed(email_factory, monkeypatch):
    _isolate_to(monkeypatch, "_recover_transactional_email")

    log = TransactionalEmailLog(
        to_email="live@example.com",
        from_email="noreply@example.com",
        content="body",
        purpose="test",
        subject="subject",
        is_send=False,
        lease_owner="live-worker",
        lease_expires_at=utc_now() + timedelta(minutes=4),
    )
    async with email_factory() as session:
        session.add(log)
        await session.commit()

    publisher = RecordingPublisher()
    outcome = await run_reconciliation_batch(
        session_factory=email_factory,
        publisher=publisher,
        enabled=True,
    )
    assert outcome.claimed == 0
    assert publisher.calls == []


@pytest_asyncio.fixture
async def bulk_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(EmailCampaign.__table__.create)
        await conn.run_sync(EmailDelivery.__table__.create)
    factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_stale_bulk_processing_is_reset_and_tick_republished(
    bulk_factory, monkeypatch
):
    _isolate_to(monkeypatch, "_recover_bulk_email")

    campaign = EmailCampaign(
        name="Campaign",
        subject="Hello",
        body_html="<p>Hi</p>",
        status=EmailCampaignStatus.processing,
        created_by=uuid4(),
    )
    stuck = EmailDelivery(
        campaign_id=campaign.id,
        user_id=uuid4(),
        email="stuck@example.com",
        status=EmailDeliveryStatus.processing,
        last_attempt_at=utc_now() - timedelta(minutes=20),
    )
    already_sent = EmailDelivery(
        campaign_id=campaign.id,
        user_id=uuid4(),
        email="sent@example.com",
        status=EmailDeliveryStatus.processing,
        sendgrid_message_id="sg-already-sent",
        last_attempt_at=utc_now() - timedelta(minutes=20),
    )
    async with bulk_factory() as session:
        session.add(campaign)
        session.add(stuck)
        session.add(already_sent)
        await session.commit()
        stuck_id = stuck.id
        sent_id = already_sent.id

    publisher = RecordingPublisher()
    outcome = await run_reconciliation_batch(
        session_factory=bulk_factory,
        publisher=publisher,
        enabled=True,
        batch_size=10,
    )
    assert outcome.claimed == 1
    assert outcome.skipped == 1
    assert publisher.calls == [(BULK_EMAIL_TICK, CeleryTaskQueue.BULK_EMAIL_QUEUE.value, [])]

    async with bulk_factory() as session:
        reset = await session.get(EmailDelivery, stuck_id)
        preserved = await session.get(EmailDelivery, sent_id)
        assert reset is not None and reset.status == EmailDeliveryStatus.pending
        assert preserved is not None
        assert preserved.status == EmailDeliveryStatus.sent
        assert preserved.sendgrid_message_id == "sg-already-sent"


@pytest_asyncio.fixture
async def campaign_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        # Avoid Postgres-only JSONB from the real model DDL on SQLite.
        await conn.execute(
            text(
                """
                CREATE TABLE notification_campaigns (
                    id CHAR(36) PRIMARY KEY,
                    notification_type_id CHAR(36) NOT NULL,
                    campaign_type VARCHAR(32) NOT NULL,
                    title VARCHAR(255) NOT NULL,
                    message TEXT NOT NULL,
                    deep_link_payload TEXT,
                    created_by_admin_id CHAR(36),
                    scheduled_at DATETIME,
                    sent_at DATETIME,
                    status VARCHAR(32) NOT NULL,
                    is_active BOOLEAN NOT NULL DEFAULT 1,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
    factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    try:
        yield factory
    finally:
        await engine.dispose()


async def _insert_campaign(
    factory,
    *,
    status: NotificationCampaignStatus,
    created_at: datetime,
    is_active: bool = True,
) -> UUID:
    campaign_id = uuid4()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO notification_campaigns (
                    id, notification_type_id, campaign_type, title, message,
                    status, is_active, created_at, updated_at
                ) VALUES (
                    :id, :notification_type_id, :campaign_type, :title, :message,
                    :status, :is_active, :created_at, :updated_at
                )
                """
            ),
            {
                "id": str(campaign_id),
                "notification_type_id": str(uuid4()),
                "campaign_type": NotificationCampaignType.announcement.value,
                "title": "Campaign",
                "message": "Body",
                "status": status.value,
                "is_active": is_active,
                "created_at": created_at,
                "updated_at": created_at,
            },
        )
        await session.commit()
    return campaign_id


@pytest.mark.asyncio
async def test_stale_draft_notification_campaign_is_republished(
    campaign_factory, monkeypatch
):
    _isolate_to(monkeypatch, "_recover_notification_campaigns")

    stale_id = await _insert_campaign(
        campaign_factory,
        status=NotificationCampaignStatus.draft,
        created_at=utc_now() - timedelta(minutes=5),
    )
    await _insert_campaign(
        campaign_factory,
        status=NotificationCampaignStatus.draft,
        created_at=utc_now(),
    )
    await _insert_campaign(
        campaign_factory,
        status=NotificationCampaignStatus.sent,
        created_at=utc_now() - timedelta(minutes=5),
    )

    publisher = RecordingPublisher()
    outcome = await run_reconciliation_batch(
        session_factory=campaign_factory,
        publisher=publisher,
        enabled=True,
        batch_size=10,
    )
    assert outcome.claimed == 1
    assert publisher.calls == [
        (
            NOTIFICATION_CAMPAIGN_DISPATCH_TASK,
            CeleryTaskQueue.NOTIFICATIONS_QUEUE.value,
            [str(stale_id)],
        )
    ]


def test_reconciliation_publishes_existing_task_names_only():
    import inspect

    import core.jobs.reconciliation_service as service

    source = inspect.getsource(service)
    assert 'name="kampulynk.reconcile.tick"' not in source
    assert EXPORT_PROCESS_TASK in source
    assert TRANSACTIONAL_EMAIL_TICK in source
    assert BULK_EMAIL_TICK in source
    assert MODERATION_TICK in source
    assert DELETION_TICK in source
    assert SPOTLIGHT_TICK in source
    assert NOTIFICATION_CAMPAIGN_DISPATCH_TASK in source


@pytest.mark.asyncio
async def test_disabled_moderation_does_not_publish(export_factory, monkeypatch):
    from types import SimpleNamespace

    _isolate_to(monkeypatch, "_recover_moderation")
    monkeypatch.setattr(
        "apps.moderation.config.settings",
        SimpleNamespace(enabled=False, batch_size=50),
    )
    publisher = RecordingPublisher()
    outcome = await run_reconciliation_batch(
        session_factory=export_factory,
        publisher=publisher,
        enabled=True,
    )
    assert outcome.claimed == 0
    assert publisher.calls == []


@pytest.mark.asyncio
async def test_completed_spotlight_date_is_not_republished(export_factory, monkeypatch):
    import core.jobs.reconciliation_service as service
    from types import SimpleNamespace

    _isolate_to(monkeypatch, "_recover_spotlight")

    class FakeSettingsService:
        async def get_persisted_settings(self, session):
            return SimpleNamespace(is_enabled=True, is_running=False)

    async def already_completed(session, business_date):
        return SimpleNamespace(business_date=business_date)

    monkeypatch.setattr(
        "apps.recommendations.services.recommendation_settings_service.RecommendationSettingsService",
        FakeSettingsService,
    )
    monkeypatch.setattr(
        "apps.learningspotlight.services.daily_run_service.fetch_completed_daily_run",
        already_completed,
    )
    monkeypatch.setattr(service, "_spotlight_lock_is_free", _noop_family)

    publisher = RecordingPublisher()
    outcome = await run_reconciliation_batch(
        session_factory=export_factory,
        publisher=publisher,
        enabled=True,
    )
    assert publisher.calls == []
    assert outcome.skipped >= 1
    assert SPOTLIGHT_TICK not in {name for name, _queue, _args in publisher.calls}


@pytest.mark.asyncio
async def test_missing_spotlight_run_republishes_on_spotlight_queue(export_factory, monkeypatch):
    import core.jobs.reconciliation_service as service
    from types import SimpleNamespace

    _isolate_to(monkeypatch, "_recover_spotlight")

    class FakeSettingsService:
        async def get_persisted_settings(self, session):
            return SimpleNamespace(is_enabled=True, is_running=False)

    async def not_completed(session, business_date):
        return None

    async def lock_free(*args, **kwargs):
        return True

    monkeypatch.setattr(
        "apps.recommendations.services.recommendation_settings_service.RecommendationSettingsService",
        FakeSettingsService,
    )
    monkeypatch.setattr(
        "apps.learningspotlight.services.daily_run_service.fetch_completed_daily_run",
        not_completed,
    )
    monkeypatch.setattr(service, "_spotlight_lock_is_free", lock_free)

    publisher = RecordingPublisher()
    outcome = await run_reconciliation_batch(
        session_factory=export_factory,
        publisher=publisher,
        enabled=True,
    )
    assert outcome.claimed == 1
    assert publisher.calls == [(SPOTLIGHT_TICK, CeleryTaskQueue.SPOTLIGHTS_QUEUE.value, [])]


_PG_SKIP_REASON: str | None = None


def _postgres_async_url() -> str | None:
    from core.database.config import settings

    url = settings.async_database_url
    if url.startswith("postgresql"):
        return url
    return None


async def _connect_postgres_engine():
    global _PG_SKIP_REASON
    if _PG_SKIP_REASON:
        pytest.skip(_PG_SKIP_REASON)
    pg_url = _postgres_async_url()
    if pg_url is None:
        _PG_SKIP_REASON = "PostgreSQL not available"
        pytest.skip(_PG_SKIP_REASON)
    from core.database.config import settings as db_settings

    engine = create_async_engine(
        pg_url,
        poolclass=NullPool,
        connect_args=db_settings.async_connect_args,
    )
    try:

        async def _ping():
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
                await conn.commit()

        await asyncio.wait_for(_ping(), timeout=5)
        async with engine.begin() as conn:
            await conn.run_sync(TransactionalEmailLog.__table__.create, checkfirst=True)
        return engine
    except Exception as exc:
        await engine.dispose()
        _PG_SKIP_REASON = f"PostgreSQL not available: {exc}"
        pytest.skip(_PG_SKIP_REASON)


@pytest.mark.asyncio
async def test_postgres_skip_locked_prevents_two_workers_claiming_same_row():
    engine = await _connect_postgres_engine()
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    marker = f"reconcile-{uuid4().hex[:12]}"
    rows = [
        TransactionalEmailLog(
            to_email=f"{marker}-{idx}@example.com",
            from_email="noreply@example.com",
            content="body",
            purpose="reconcile-test",
            subject=marker,
            is_send=False,
            lease_owner="dead-worker",
            lease_expires_at=utc_now() - timedelta(minutes=15),
        )
        for idx in range(2)
    ]
    try:
        async with factory() as session:
            session.add_all(rows)
            await session.commit()
            ids = [row.id for row in rows]

        from sqlmodel import select

        stmt = (
            select(TransactionalEmailLog.id)
            .where(TransactionalEmailLog.subject == marker)
            .where(TransactionalEmailLog.is_send.is_(False))
            .order_by(TransactionalEmailLog.created_at.asc())
            .limit(1)
        )

        async with factory() as first, factory() as second:
            locked_first = await _lock_ids(first, stmt)
            locked_second = await _lock_ids(second, stmt)
            await first.commit()
            await second.commit()

        assert len(locked_first) == 1
        assert len(locked_second) == 1
        assert locked_first[0] != locked_second[0]
        assert set(locked_first + locked_second) == set(ids)
    finally:
        async with factory() as session:
            await session.execute(
                text("DELETE FROM transactional_email_log WHERE subject = :subject"),
                {"subject": marker},
            )
            await session.commit()
        await engine.dispose()
