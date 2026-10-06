from __future__ import annotations

import asyncio
import inspect
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from core.celery_worker.config import CeleryTaskQueue

import pytest
from sqlalchemy.sql.dml import Update

os.environ.setdefault("CELERY_BROKER_URL", "redis://localhost:6379/0")

from apps.export.enums import DataExportStatus
from apps.export.models import DataExportRequest
from apps.export.tasks import (
    _process_export,
    enqueue_export_processing,
    process_export_task,
)
from tests.unit.conftest import FakeScalarResult


class FakeExportStorage:
    def __init__(self) -> None:
        self.uploaded: list[str] = []
        self.urls: list[tuple[str, int]] = []

    def upload(self, storage_key: str, data: bytes, content_type: str = "application/zip") -> str:
        self.uploaded.append(storage_key)
        return storage_key

    def generate_download_url(self, storage_key: str, expires_in: int) -> str:
        self.urls.append((storage_key, expires_in))
        return f"https://example.test/{storage_key}?sig=test"


class FakeSession:
    def __init__(self, results) -> None:
        self._results = list(results)
        self.statements = []
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def execute(self, stmt):
        self.statements.append(stmt)
        if not self._results:
            return FakeScalarResult()
        return self._results.pop(0)

    async def commit(self):
        self.commits += 1


class FakeRuntime:
    def __init__(self, session: FakeSession) -> None:
        self._session = session

    def session_factory(self):
        return self._session


def _queued_export(export_id=None, user_id=None) -> DataExportRequest:
    return DataExportRequest(
        id=export_id or uuid4(),
        user_id=user_id or uuid4(),
        status=DataExportStatus.queued,
    )


def test_task_receives_export_id_and_uses_celery_task_id_as_lease_owner(monkeypatch):
    seen: dict = {}

    async def fake_process(runtime, export_id, *, lease_owner, storage=None):
        seen["export_id"] = export_id
        seen["lease_owner"] = lease_owner

    class Runner:
        def run(self, coro):
            return asyncio.run(coro)

    class Runtime:
        runner = Runner()

        def close(self):
            seen["closed"] = True

    monkeypatch.setattr("apps.export.tasks.create_worker_runtime", lambda: Runtime())
    monkeypatch.setattr("apps.export.tasks._process_export", fake_process)

    export_id = uuid4()
    process_export_task.push_request(id="celery-task-123")
    try:
        process_export_task.run(str(export_id))
    finally:
        process_export_task.pop_request()

    assert seen["export_id"] == export_id
    assert seen["lease_owner"] == "celery-task-123"
    assert seen["closed"] is True


def test_task_registration_and_payload_contains_only_export_id():
    assert process_export_task.name == "kampulynk.export.process"
    queue = getattr(process_export_task, "queue", None)
    if queue is None:
        queue = (process_export_task._get_exec_options() or {}).get("queue")
    assert queue == CeleryTaskQueue.EXPORTS_QUEUE.value

    params = list(inspect.signature(process_export_task.run).parameters)
    assert "export_id" in params
    forbidden = {
        "password",
        "zip_password",
        "url",
        "download_url",
        "presigned_url",
        "session",
        "db",
        "user",
        "orm",
    }
    assert forbidden.isdisjoint(set(params))


def test_task_module_does_not_use_api_global_session_factory():
    import apps.export.tasks as tasks_mod

    source = inspect.getsource(tasks_mod)
    assert "async_session_factory" not in source
    assert "create_worker_runtime" in source


@pytest.mark.asyncio
async def test_queued_export_is_claimed_and_processed(export_tmp_path):
    export_id = uuid4()
    user_id = uuid4()
    record = _queued_export(export_id=export_id, user_id=user_id)
    storage = FakeExportStorage()
    temp_file = export_tmp_path / "export.zip"
    temp_file.write_bytes(b"PKZIP")
    session = FakeSession(
        [
            FakeScalarResult(value=record),
            FakeScalarResult(value=None),
            SimpleNamespace(rowcount=1),
        ]
    )
    runtime = FakeRuntime(session)

    with (
        patch("apps.export.tasks.claim_export", new=AsyncMock(return_value=True)) as claim,
        patch(
            "apps.export.tasks.DataExportBuilder.build_encrypted_zip_bytes",
            new=AsyncMock(return_value=b"PKZIP"),
        ),
        patch("apps.export.tasks._write_temp_zip", return_value=temp_file),
        patch("apps.export.tasks._queue_export_ready_email", new=AsyncMock()) as queue_email,
    ):
        await _process_export(
            runtime,  # type: ignore[arg-type]
            export_id,
            lease_owner="worker-1",
            storage=storage,
        )

    claim.assert_awaited_once()
    assert claim.await_args.kwargs["export_id"] == export_id
    assert claim.await_args.kwargs["lease_owner"] == "worker-1"
    assert storage.uploaded == [f"exports/{user_id}/{export_id}.zip"]
    assert storage.urls == [(f"exports/{user_id}/{export_id}.zip", 604800)]
    assert session.commits >= 2
    assert any(isinstance(stmt, Update) for stmt in session.statements)
    queue_email.assert_awaited_once()
    email_kwargs = queue_email.await_args.kwargs
    assert email_kwargs["zip_password"]
    assert "sig=test" in email_kwargs["presigned_url"]
    assert not temp_file.exists()


@pytest.mark.asyncio
async def test_export_that_cannot_be_claimed_is_skipped():
    export_id = uuid4()
    session = FakeSession([])
    runtime = FakeRuntime(session)
    storage = FakeExportStorage()

    with (
        patch("apps.export.tasks.claim_export", new=AsyncMock(return_value=False)) as claim,
        patch(
            "apps.export.tasks.DataExportBuilder.build_encrypted_zip_bytes",
            new=AsyncMock(return_value=b"PKZIP"),
        ) as build_zip,
        patch("apps.export.tasks._queue_export_ready_email", new=AsyncMock()) as queue_email,
    ):
        await _process_export(
            runtime,  # type: ignore[arg-type]
            export_id,
            lease_owner="worker-2",
            storage=storage,
        )

    claim.assert_awaited_once()
    build_zip.assert_not_awaited()
    queue_email.assert_not_awaited()
    assert storage.uploaded == []
    assert session.commits == 0


@pytest.mark.asyncio
async def test_failure_updates_export_to_failed():
    export_id = uuid4()
    record = _queued_export(export_id=export_id)
    session = FakeSession(
        [
            FakeScalarResult(value=record),
            FakeScalarResult(value=None),
            SimpleNamespace(rowcount=1),
        ]
    )
    runtime = FakeRuntime(session)

    with (
        patch("apps.export.tasks.claim_export", new=AsyncMock(return_value=True)),
        patch(
            "apps.export.tasks.DataExportBuilder.build_encrypted_zip_bytes",
            new=AsyncMock(side_effect=RuntimeError("zip boom")),
        ),
        patch("apps.export.tasks._queue_export_ready_email", new=AsyncMock()) as queue_email,
    ):
        await _process_export(
            runtime,  # type: ignore[arg-type]
            export_id,
            lease_owner="worker-3",
            storage=FakeExportStorage(),
        )

    fail_stmt = next(stmt for stmt in session.statements if isinstance(stmt, Update))
    compiled = str(fail_stmt.compile(compile_kwargs={"literal_binds": True})).lower()
    assert "failed" in compiled
    assert "lease_owner" in compiled
    assert "zip boom" in compiled
    assert session.commits >= 2
    queue_email.assert_not_awaited()


@pytest.mark.asyncio
async def test_temporary_files_are_cleaned_up_on_upload_failure(export_tmp_path):
    export_id = uuid4()
    user_id = uuid4()
    record = _queued_export(export_id=export_id, user_id=user_id)
    temp_file = export_tmp_path / "export.zip"
    temp_file.write_bytes(b"PKZIP")
    session = FakeSession(
        [
            FakeScalarResult(value=record),
            FakeScalarResult(value=None),
            SimpleNamespace(rowcount=1),
        ]
    )
    runtime = FakeRuntime(session)
    storage = FakeExportStorage()
    storage.upload = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("upload failed"))

    with (
        patch("apps.export.tasks.claim_export", new=AsyncMock(return_value=True)),
        patch(
            "apps.export.tasks.DataExportBuilder.build_encrypted_zip_bytes",
            new=AsyncMock(return_value=b"PKZIP"),
        ),
        patch("apps.export.tasks._write_temp_zip", return_value=temp_file),
    ):
        await _process_export(
            runtime,  # type: ignore[arg-type]
            export_id,
            lease_owner="worker-4",
            storage=storage,
        )

    assert not temp_file.exists()


def test_celery_task_arguments_exclude_passwords_and_signed_urls():
    args = [str(uuid4())]
    bound = process_export_task.s(*args)
    payload = bound.args
    assert payload == tuple(args)
    assert len(payload) == 1
    joined = " ".join(str(item) for item in payload)
    assert "password" not in joined.lower()
    assert "http" not in joined.lower()
    assert "X-Amz-Signature" not in joined


def test_enqueue_export_processing_publishes_export_id_only():
    export_id = uuid4()
    seen: dict = {}

    class FakeResult:
        id = "celery-task-456"

    def fake_apply_async(*, args, queue):
        seen["args"] = args
        seen["queue"] = queue
        return FakeResult()

    with patch.object(process_export_task, "apply_async", side_effect=fake_apply_async):
        task_id = enqueue_export_processing(export_id)

    assert task_id == "celery-task-456"
    assert seen["args"] == [str(export_id)]
    assert seen["queue"] == CeleryTaskQueue.EXPORTS_QUEUE.value
    assert seen["queue"] != CeleryTaskQueue.BACKGROUND_QUEUE.value
    assert len(seen["args"]) == 1
    assert "password" not in seen["args"][0]
    assert "http" not in seen["args"][0]


def test_enqueue_export_processing_returns_none_on_broker_failure():
    def fake_apply_async(*, args, queue):
        raise ConnectionError("redis unavailable")

    with patch.object(process_export_task, "apply_async", side_effect=fake_apply_async):
        task_id = enqueue_export_processing(uuid4())

    assert task_id is None


def test_enqueue_export_processing_rejects_invalid_export_id():
    with patch.object(process_export_task, "apply_async") as apply_async:
        task_id = enqueue_export_processing("not-a-uuid")

    assert task_id is None
    apply_async.assert_not_called()
