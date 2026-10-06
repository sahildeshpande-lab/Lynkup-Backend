from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from core.celery_worker.config import CeleryTaskQueue

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.sql.dml import Update

from apps.accounts.db_models import TransactionalEmailLog
from core.celery_worker.celery_app import celery_app
from core.email_service import (
    _complete_transactional_email_if_owner,
    process_pending_emails,
    process_transactional_emails,
    run_transactional_email_tick,
)
from core.email_tasks import transactional_email_tick
from core.jobs.claims import claim_transactional_email


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@pytest_asyncio.fixture
async def email_session_factory():
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


async def _insert_email(
    factory,
    *,
    is_send: bool = False,
    lease_owner: str | None = None,
    lease_expires_at: datetime | None = None,
    attempt_count: int = 0,
    to_email: str | None = None,
) -> TransactionalEmailLog:
    log = TransactionalEmailLog(
        to_email=to_email or f"pending-{uuid4().hex[:8]}@example.com",
        from_email="noreply@example.com",
        content="body",
        purpose="test",
        subject="subject",
        is_send=is_send,
        attempt_count=attempt_count,
        lease_owner=lease_owner,
        lease_expires_at=lease_expires_at,
    )
    async with factory() as session:
        session.add(log)
        await session.commit()
        await session.refresh(log)
        return log


def test_task_registration_name_and_transactional_queue():
    assert transactional_email_tick.name == "kampulynk.email.transactional.tick"
    queue = getattr(transactional_email_tick, "queue", None)
    if queue is None:
        queue = (transactional_email_tick._get_exec_options() or {}).get("queue")
    assert queue == CeleryTaskQueue.TRANSACTIONAL_QUEUE.value
    assert transactional_email_tick.ignore_result is True
    assert "core.email_tasks" in celery_app.conf.imports


def test_beat_schedule_is_every_60_seconds():
    entry = celery_app.conf.beat_schedule["kampulynk.email.transactional.tick"]
    assert entry["task"] == "kampulynk.email.transactional.tick"
    schedule = entry["schedule"]
    seconds = getattr(schedule, "run_every", schedule)
    if hasattr(seconds, "total_seconds"):
        seconds = seconds.total_seconds()
    assert float(seconds) == 60.0
    assert entry["options"]["queue"] == CeleryTaskQueue.TRANSACTIONAL_QUEUE.value
    assert "kampulynk.email.transactional.tick" in celery_app.conf.beat_schedule


def test_task_module_uses_worker_owned_runtime_not_api_session():
    import core.email_tasks as tasks_mod

    source = inspect.getsource(tasks_mod)
    assert "create_worker_runtime" in source
    assert "async_session_factory" not in source
    assert "asyncio.run" not in source
    params = list(inspect.signature(transactional_email_tick.run).parameters)
    forbidden = {"session", "db", "orm", "html_body", "content"}
    assert forbidden.isdisjoint(set(params))


def test_task_not_validated_only_via_eager_mode():
    assert celery_app.conf.task_always_eager in (False, None)
    assert celery_app.conf.task_ignore_result is True


def test_task_can_publish_to_real_broker():
    from core.celery_worker.config import settings as celery_settings

    try:
        import redis

        client = redis.Redis.from_url(
            celery_settings.celery_broker_url,
            socket_connect_timeout=1,
        )
        client.ping()
    except Exception:
        pytest.skip("Redis broker not available")

    result = transactional_email_tick.apply_async()
    assert result.id
    assert celery_app.conf.task_always_eager in (False, None)


def test_successful_task_execution_uses_runtime(monkeypatch):
    seen: dict = {}

    async def fake_run(runtime, *, lease_owner):
        seen["lease_owner"] = lease_owner
        seen["runtime"] = runtime
        return 0

    class Runtime:
        class Runner:
            def run(self, coro):
                return asyncio.run(coro)

        runner = Runner()

        def close(self):
            seen["closed"] = True

    monkeypatch.setattr("core.email_tasks.create_worker_runtime", lambda: Runtime())
    monkeypatch.setattr("core.email_tasks._run_transactional_email_tick", fake_run)

    transactional_email_tick.push_request(id="celery-task-email-1")
    try:
        transactional_email_tick.run()
    finally:
        transactional_email_tick.pop_request()

    assert seen["lease_owner"] == "celery-task-email-1"
    assert seen["closed"] is True
    assert seen["runtime"] is not None


def test_task_level_failure_is_visible_to_celery(monkeypatch):
    async def boom(runtime, *, lease_owner):
        raise RuntimeError("transactional email infrastructure failure")

    class Runtime:
        class Runner:
            def run(self, coro):
                return asyncio.run(coro)

        runner = Runner()

        def close(self):
            pass

    monkeypatch.setattr("core.email_tasks.create_worker_runtime", lambda: Runtime())
    monkeypatch.setattr("core.email_tasks._run_transactional_email_tick", boom)

    transactional_email_tick.push_request(id="celery-task-email-fail")
    try:
        with pytest.raises(RuntimeError, match="infrastructure failure"):
            transactional_email_tick.run()
    finally:
        transactional_email_tick.pop_request()


@pytest.mark.asyncio
async def test_pending_email_is_claimed_and_sent(email_session_factory, monkeypatch):
    log = await _insert_email(email_session_factory)

    async def mock_send(*args, **kwargs):
        return True

    monkeypatch.setattr(
        "core.email_service._actually_send_email_via_sendgrid",
        mock_send,
    )

    processed = await process_pending_emails(
        limit=10,
        session_factory=email_session_factory,
        lease_owner="worker-1",
    )
    assert processed == 1

    async with email_session_factory() as session:
        stored = await session.get(TransactionalEmailLog, log.id)
        assert stored is not None
        assert stored.is_send is True
        assert stored.lease_owner == "worker-1"
        assert stored.attempt_count == 1


@pytest.mark.asyncio
async def test_duplicate_execution_cannot_claim_same_email(email_session_factory):
    log = await _insert_email(email_session_factory)

    async with email_session_factory() as session:
        first = await claim_transactional_email(
            session,
            email_id=log.id,
            lease_owner="worker-a",
        )
        second = await claim_transactional_email(
            session,
            email_id=log.id,
            lease_owner="worker-b",
        )

    assert first.claimed is True
    assert second.claimed is False

    async with email_session_factory() as session:
        stored = await session.get(TransactionalEmailLog, log.id)
        assert stored.lease_owner == "worker-a"
        assert stored.attempt_count == 1
        assert stored.is_send is False


@pytest.mark.asyncio
async def test_expired_lease_can_be_reclaimed(email_session_factory):
    log = await _insert_email(
        email_session_factory,
        lease_owner="old-worker",
        lease_expires_at=utc_now() - timedelta(minutes=5),
        attempt_count=1,
    )

    async with email_session_factory() as session:
        result = await claim_transactional_email(
            session,
            email_id=log.id,
            lease_owner="new-worker",
        )

    assert result.claimed is True
    async with email_session_factory() as session:
        stored = await session.get(TransactionalEmailLog, log.id)
        assert stored.lease_owner == "new-worker"
        assert stored.attempt_count == 2


@pytest.mark.asyncio
async def test_completed_email_is_not_claimed_again(email_session_factory):
    log = await _insert_email(email_session_factory, is_send=True)

    async with email_session_factory() as session:
        result = await claim_transactional_email(
            session,
            email_id=log.id,
            lease_owner="worker-1",
        )

    assert result.claimed is False
    async with email_session_factory() as session:
        stored = await session.get(TransactionalEmailLog, log.id)
        assert stored.lease_owner is None
        assert stored.attempt_count == 0


@pytest.mark.asyncio
async def test_stale_worker_cannot_overwrite_newer_owner(email_session_factory):
    log = await _insert_email(
        email_session_factory,
        lease_owner="worker-b",
        lease_expires_at=utc_now() + timedelta(minutes=10),
    )

    async with email_session_factory() as session:
        updated = await _complete_transactional_email_if_owner(
            session,
            log_id=log.id,
            lease_owner="worker-a",
            is_send=True,
        )
        await session.commit()

    assert updated is False
    async with email_session_factory() as session:
        stored = await session.get(TransactionalEmailLog, log.id)
        assert stored.is_send is False
        assert stored.lease_owner == "worker-b"


@pytest.mark.asyncio
async def test_no_pending_email_returns_zero(email_session_factory, monkeypatch):
    sent = {"n": 0}

    async def mock_send(*args, **kwargs):
        sent["n"] += 1
        return True

    monkeypatch.setattr(
        "core.email_service._actually_send_email_via_sendgrid",
        mock_send,
    )
    processed = await process_pending_emails(
        limit=10,
        session_factory=email_session_factory,
        lease_owner="worker-1",
    )
    assert processed == 0
    assert sent["n"] == 0


@pytest.mark.asyncio
async def test_expected_send_failure_does_not_fail_the_tick(
    email_session_factory, monkeypatch
):
    log = await _insert_email(email_session_factory)

    async def mock_send(*args, **kwargs):
        return False

    monkeypatch.setattr(
        "core.email_service._actually_send_email_via_sendgrid",
        mock_send,
    )
    processed = await process_pending_emails(
        limit=10,
        session_factory=email_session_factory,
        lease_owner="worker-1",
    )
    assert processed == 1
    async with email_session_factory() as session:
        stored = await session.get(TransactionalEmailLog, log.id)
        assert stored.is_send is False
        assert stored.lease_owner == "worker-1"


@pytest.mark.asyncio
async def test_run_tick_propagates_infrastructure_failure(monkeypatch):
    async def boom(**kwargs):
        raise RuntimeError("database unavailable")

    async def _ok(*args, **kwargs):
        return 0

    monkeypatch.setattr(
        "apps.connections.services.connection_reminder_service.process_connection_reminders",
        _ok,
    )
    monkeypatch.setattr(
        "apps.profiles.services.graduation_email_service.process_graduation_completion_emails",
        _ok,
    )
    monkeypatch.setattr("core.email_service.process_pending_emails", boom)

    with pytest.raises(RuntimeError, match="database unavailable"):
        await run_transactional_email_tick(lease_owner="worker-1")


@pytest.mark.asyncio
async def test_legacy_cron_wrapper_still_swallows_outer_failure(monkeypatch):
    async def boom(**kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr("core.email_service.run_transactional_email_tick", boom)
    await process_transactional_emails()


@pytest.mark.asyncio
async def test_tick_passes_worker_session_factory_to_producers_and_pending(monkeypatch):
    factory = object()
    seen: dict = {}

    async def fake_reminders(*, limit=None, session_factory=None):
        seen["reminder_factory"] = session_factory
        return 0

    async def fake_grad(*, limit=None, session_factory=None):
        seen["grad_factory"] = session_factory
        return 0

    async def fake_pending(*, limit=10, session_factory=None, lease_owner=None):
        seen["pending_factory"] = session_factory
        seen["lease_owner"] = lease_owner
        return 3

    monkeypatch.setattr(
        "apps.connections.services.connection_reminder_service.process_connection_reminders",
        fake_reminders,
    )
    monkeypatch.setattr(
        "apps.profiles.services.graduation_email_service.process_graduation_completion_emails",
        fake_grad,
    )
    monkeypatch.setattr("core.email_service.process_pending_emails", fake_pending)

    processed = await run_transactional_email_tick(
        session_factory=factory,
        lease_owner="worker-runtime-1",
    )

    assert processed == 3
    assert seen["reminder_factory"] is factory
    assert seen["grad_factory"] is factory
    assert seen["pending_factory"] is factory
    assert seen["lease_owner"] == "worker-runtime-1"


def test_run_tick_source_forwards_session_factory_to_nested_db_work():
    source = inspect.getsource(run_transactional_email_tick)
    assert "process_connection_reminders(session_factory=session_factory)" in source
    assert "process_graduation_completion_emails(session_factory=session_factory)" in source
    assert "session_factory=session_factory" in source


def test_celery_task_forwards_runtime_session_factory():
    import core.email_tasks as tasks_mod

    source = inspect.getsource(tasks_mod._run_transactional_email_tick)
    assert "session_factory=runtime.session_factory" in source
    assert "async_session_factory" not in source


def test_repeated_celery_ticks_do_not_reuse_a_closed_event_loop(monkeypatch):
    runtimes: list = []
    seen_loops: list = []
    seen_factories: list = []

    class Runtime:
        def __init__(self):
            self.runner = asyncio.Runner()
            self.session_factory = object()
            self.engine = object()
            self.loop = self.runner.get_loop()
            runtimes.append(self)

        def close(self):
            self.runner.close()

    async def fake_run(runtime, *, lease_owner):
        loop = asyncio.get_running_loop()
        assert loop is runtime.loop
        assert loop.is_closed() is False
        seen_loops.append(loop)
        seen_factories.append(runtime.session_factory)
        return 0

    monkeypatch.setattr("core.email_tasks.create_worker_runtime", Runtime)
    monkeypatch.setattr("core.email_tasks._run_transactional_email_tick", fake_run)

    transactional_email_tick.run()
    assert runtimes[0].loop.is_closed() is True

    transactional_email_tick.run()
    assert runtimes[1].loop.is_closed() is True

    assert seen_loops[0] is not seen_loops[1]
    assert seen_factories[0] is not seen_factories[1]
    assert runtimes[0] is not runtimes[1]


def test_celery_tick_uses_runtime_owned_engine_and_session_factory(monkeypatch):
    seen: dict = {}

    class Runtime:
        def __init__(self):
            self.runner = asyncio.Runner()
            self.engine = object()
            self.session_factory = object()
            self.loop = self.runner.get_loop()
            seen["runtime"] = self

        def close(self):
            self.runner.close()
            seen["closed"] = True

    async def fake_tick(*, session_factory=None, lease_owner=None, limit=10):
        loop = asyncio.get_running_loop()
        runtime = seen["runtime"]
        assert loop is runtime.loop
        assert loop.is_closed() is False
        seen["session_factory"] = session_factory
        seen["lease_owner"] = lease_owner
        return 0

    monkeypatch.setattr("core.email_tasks.create_worker_runtime", Runtime)
    monkeypatch.setattr("core.email_service.run_transactional_email_tick", fake_tick)

    transactional_email_tick.push_request(id="celery-runtime-owner")
    try:
        transactional_email_tick.run()
    finally:
        transactional_email_tick.pop_request()

    runtime = seen["runtime"]
    assert seen["session_factory"] is runtime.session_factory
    assert seen["lease_owner"] == "celery-runtime-owner"
    assert seen["closed"] is True
    assert runtime.loop.is_closed() is True


@pytest.mark.asyncio
async def test_complete_if_owner_sql_requires_lease_owner(mock_db):
    db = mock_db(SimpleNamespace(rowcount=0))
    email_id = uuid4()

    updated = await _complete_transactional_email_if_owner(
        db,
        log_id=email_id,
        lease_owner="worker-a",
        is_send=True,
    )

    assert updated is False
    stmt = db.execute.await_args.args[0]
    assert isinstance(stmt, Update)
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True})).lower()
    assert "lease_owner" in compiled
    assert "worker-a" in compiled
