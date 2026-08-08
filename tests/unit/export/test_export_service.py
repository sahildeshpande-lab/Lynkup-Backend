from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from unittest.mock import AsyncMock, Mock, patch

import pytest

from apps.accounts.db_models import User
from apps.export.enums import DataExportStatus
from apps.export.models import DataExportRequest
from apps.export.service import DataExportService
from apps.export.storage import LocalExportStorage
from common.exceptions import ApiError
from tests.unit.conftest import FakeScalarResult


def _user(user_id=None) -> User:
    return User(
        id=user_id or uuid4(),
        email="owner@example.com",
        role="user",
        firebase_uid="uid-1",
    )


@pytest.mark.asyncio
async def test_request_export_creates_queued_record(mock_db):
    user = _user()
    db = mock_db(FakeScalarResult(value=None))
    storage = LocalExportStorage(base_path=Path("./storage/exports-test"))
    service = DataExportService(storage=storage)

    with patch.object(db, "refresh", new=AsyncMock(side_effect=lambda obj: setattr(obj, "id", uuid4()) or None)):
        result = await service.request_export(user=user, db=db)

    assert result.status == DataExportStatus.queued
    assert db.add.called
    assert db.commit.await_count >= 1


@pytest.mark.asyncio
async def test_request_export_blocks_concurrent(mock_db):
    user = _user()
    existing = DataExportRequest(
        id=uuid4(),
        user_id=user.id,
        status=DataExportStatus.processing,
    )
    db = mock_db(FakeScalarResult(value=existing))
    service = DataExportService()

    with pytest.raises(ApiError, match="already in progress"):
        await service.request_export(user=user, db=db)


@pytest.mark.asyncio
async def test_get_export_status_hides_internal_fields(mock_db):
    user = _user()
    export_id = uuid4()
    record = DataExportRequest(
        id=export_id,
        user_id=user.id,
        status=DataExportStatus.completed,
        requested_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        download_expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        file_size_bytes=1234,
        storage_key=f"exports/{export_id}.zip",
        error_message="secret",
    )
    db = mock_db(FakeScalarResult(value=record))
    service = DataExportService()

    data = await service.get_export_status(user=user, export_id=export_id, db=db)
    dumped = data.model_dump()
    assert dumped["status"] == DataExportStatus.completed
    assert "storage_key" not in dumped
    assert "error_message" not in dumped


@pytest.mark.asyncio
async def test_get_export_status_rejects_other_user(mock_db):
    owner = _user()
    other = _user()
    export_id = uuid4()
    record = DataExportRequest(
        id=export_id,
        user_id=owner.id,
        status=DataExportStatus.completed,
    )
    db = mock_db(FakeScalarResult(value=record))
    service = DataExportService()

    with pytest.raises(ApiError, match="not found"):
        await service.get_export_status(user=other, export_id=export_id, db=db)


@pytest.mark.asyncio
async def test_process_export_marks_failed_on_error(export_tmp_path: Path):
    export_id = uuid4()
    user_id = uuid4()
    record = DataExportRequest(
        id=export_id,
        user_id=user_id,
        status=DataExportStatus.queued,
    )
    storage = LocalExportStorage(base_path=export_tmp_path)
    service = DataExportService(storage=storage)

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.execute = AsyncMock(return_value=FakeScalarResult(value=record))
    session.add = Mock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    with patch("apps.export.service.async_session_factory", return_value=session):
        with patch(
            "apps.export.service.DataExportBuilder.build_zip_bytes",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            await service.process_export(export_id)

    assert record.status == DataExportStatus.failed
    assert record.error_message
    assert "boom" in record.error_message


@pytest.mark.asyncio
async def test_process_export_completes_and_queues_email(export_tmp_path: Path):
    export_id = uuid4()
    user_id = uuid4()
    record = DataExportRequest(
        id=export_id,
        user_id=user_id,
        status=DataExportStatus.queued,
    )
    storage = LocalExportStorage(base_path=export_tmp_path)
    service = DataExportService(storage=storage)

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.execute = AsyncMock(return_value=FakeScalarResult(value=record))
    session.add = Mock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    with patch("apps.export.service.async_session_factory", return_value=session):
        with patch(
            "apps.export.service.DataExportBuilder.build_zip_bytes",
            new=AsyncMock(return_value=b"PKZIP"),
        ):
            with patch.object(
                service,
                "_queue_ready_email",
                new=AsyncMock(),
            ) as queue_email:
                await service.process_export(export_id)
                queue_email.assert_awaited()

    assert record.status == DataExportStatus.completed
    assert record.storage_key == f"exports/{export_id}.zip"
    assert record.file_size_bytes == 5
    assert record.download_expires_at is not None
    assert storage.exists(record.storage_key)


@pytest.mark.asyncio
async def test_queue_ready_email_uses_base_url_and_attachment(export_tmp_path: Path, mock_db):
    export_id = uuid4()
    user = _user()
    storage = LocalExportStorage(base_path=export_tmp_path)
    key = f"exports/{export_id}.zip"
    storage.save(key, b"zip-bytes")
    record = DataExportRequest(
        id=export_id,
        user_id=user.id,
        status=DataExportStatus.completed,
        storage_key=key,
        completed_at=datetime.now(timezone.utc),
        download_expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db = mock_db(FakeScalarResult(value=user))
    service = DataExportService(storage=storage)

    with patch(
        "apps.export.service.email_settings.base_url",
        "https://lynkup-backend-311u.onrender.com",
    ):
        with patch(
            "apps.export.service._queue_email",
            new=AsyncMock(),
        ) as queue_email:
            with patch(
                "apps.export.service._render_email_layout",
                side_effect=lambda title, body_html: body_html,
            ):
                await service._queue_ready_email(db=db, export_request=record)

    queue_email.assert_awaited_once()
    args, kwargs = queue_email.await_args
    assert args[0] == user.email
    assert kwargs.get("purpose") == "Data Export Ready"
    assert kwargs["attachment"] == str(storage.absolute_path(key))
    html_body = args[2]
    assert f"https://lynkup-backend-311u.onrender.com/{export_id}" in html_body
    assert "Your KampuLynk data export is ready" in args[1]


@pytest.mark.asyncio
async def test_cleanup_expired_exports(export_tmp_path: Path, mock_db):
    export_id = uuid4()
    storage = LocalExportStorage(base_path=export_tmp_path)
    key = f"exports/{export_id}.zip"
    storage.save(key, b"old-zip")
    record = DataExportRequest(
        id=export_id,
        user_id=uuid4(),
        status=DataExportStatus.completed,
        storage_key=key,
        download_expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db = mock_db(FakeScalarResult(values=[record]))
    service = DataExportService(storage=storage)

    cleaned = await service.cleanup_expired_exports(db=db)
    assert cleaned == 1
    assert record.status == DataExportStatus.expired
    assert record.storage_key is None
    assert not storage.exists(key)
