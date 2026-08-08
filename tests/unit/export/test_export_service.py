from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from unittest.mock import AsyncMock, Mock, patch

import pytest

from apps.accounts.db_models import User
from apps.export.enums import DataExportStatus
from apps.export.models import DataExportRequest
from apps.export.service import DataExportService, build_export_storage_key
from apps.export.storage import ExportStorage
from common.exceptions import ApiError
from tests.unit.conftest import FakeScalarResult


class FakeSpacesStorage(ExportStorage):
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.uploaded_keys: list[str] = []

    def upload(self, storage_key: str, data: bytes, content_type: str = "application/zip") -> str:
        self.objects[storage_key] = data
        self.uploaded_keys.append(storage_key)
        return storage_key

    def exists(self, storage_key: str) -> bool:
        return storage_key in self.objects

    def generate_download_url(self, storage_key: str, expires_in: int | None = None) -> str:
        return f"https://signed.example/{storage_key}?X-Amz-Signature=test&exp={expires_in}"

    def delete(self, storage_key: str) -> None:
        self.deleted.append(storage_key)
        self.objects.pop(storage_key, None)


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
    service = DataExportService(storage=FakeSpacesStorage())

    with patch.object(
        db,
        "refresh",
        new=AsyncMock(side_effect=lambda obj: setattr(obj, "id", uuid4()) or None),
    ):
        result = await service.request_export(user=user, db=db)

    assert result.status == DataExportStatus.queued


@pytest.mark.asyncio
async def test_request_export_blocks_concurrent(mock_db):
    user = _user()
    existing = DataExportRequest(
        id=uuid4(),
        user_id=user.id,
        status=DataExportStatus.processing,
    )
    db = mock_db(FakeScalarResult(value=existing))
    service = DataExportService(storage=FakeSpacesStorage())

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
        storage_key=f"exports/{user.id}/{export_id}.zip",
        error_message="secret",
    )
    db = mock_db(FakeScalarResult(value=record))
    service = DataExportService(storage=FakeSpacesStorage())

    data = await service.get_export_status(user=user, export_id=export_id, db=db)
    dumped = data.model_dump()
    assert "storage_key" not in dumped
    assert "error_message" not in dumped


@pytest.mark.asyncio
async def test_download_rejects_wrong_user(mock_db):
    owner = _user()
    other = _user()
    export_id = uuid4()
    record = DataExportRequest(
        id=export_id,
        user_id=owner.id,
        status=DataExportStatus.completed,
        storage_key=f"exports/{owner.id}/{export_id}.zip",
        download_expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    db = mock_db(FakeScalarResult(value=record))
    service = DataExportService(storage=FakeSpacesStorage())

    with pytest.raises(ApiError, match="not found"):
        await service.get_download_redirect_url(user=other, export_id=export_id, db=db)


@pytest.mark.asyncio
async def test_download_rejects_expired(mock_db):
    user = _user()
    export_id = uuid4()
    key = f"exports/{user.id}/{export_id}.zip"
    storage = FakeSpacesStorage()
    storage.objects[key] = b"zip"
    record = DataExportRequest(
        id=export_id,
        user_id=user.id,
        status=DataExportStatus.completed,
        storage_key=key,
        download_expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db = mock_db(FakeScalarResult(value=record))
    service = DataExportService(storage=storage)

    with pytest.raises(ApiError, match="expired"):
        await service.get_download_redirect_url(user=user, export_id=export_id, db=db)


@pytest.mark.asyncio
async def test_download_rejects_missing_object(mock_db):
    user = _user()
    export_id = uuid4()
    record = DataExportRequest(
        id=export_id,
        user_id=user.id,
        status=DataExportStatus.completed,
        storage_key=f"exports/{user.id}/{export_id}.zip",
        download_expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    db = mock_db(FakeScalarResult(value=record))
    service = DataExportService(storage=FakeSpacesStorage())

    with pytest.raises(ApiError, match="no longer available"):
        await service.get_download_redirect_url(user=user, export_id=export_id, db=db)


@pytest.mark.asyncio
async def test_download_completed_returns_signed_url(mock_db):
    user = _user()
    export_id = uuid4()
    key = f"exports/{user.id}/{export_id}.zip"
    storage = FakeSpacesStorage()
    storage.objects[key] = b"zip"
    record = DataExportRequest(
        id=export_id,
        user_id=user.id,
        status=DataExportStatus.completed,
        storage_key=key,
        download_expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    db = mock_db(FakeScalarResult(value=record))
    service = DataExportService(storage=storage)

    url = await service.get_download_redirect_url(user=user, export_id=export_id, db=db)
    assert "X-Amz-Signature" in url
    assert "cdn.digitaloceanspaces.com" not in url
    assert key in storage.objects  # not deleted on signed URL generation


@pytest.mark.asyncio
async def test_process_export_uploads_to_spaces_key_and_deletes_temp():
    export_id = uuid4()
    user_id = uuid4()
    record = DataExportRequest(
        id=export_id,
        user_id=user_id,
        status=DataExportStatus.queued,
    )
    storage = FakeSpacesStorage()
    service = DataExportService(storage=storage)

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.execute = AsyncMock(return_value=FakeScalarResult(value=record))
    session.add = Mock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    temp_file = Path("temp_export_test.zip")
    temp_file.write_bytes(b"PKZIP")

    with patch("apps.export.service.async_session_factory", return_value=session):
        with patch(
            "apps.export.service.DataExportBuilder.build_zip_bytes",
            new=AsyncMock(return_value=b"PKZIP"),
        ):
            with patch(
                "apps.export.service.write_temp_zip",
                return_value=temp_file,
            ):
                with patch.object(service, "_queue_ready_email", new=AsyncMock()) as queue_email:
                    await service.process_export(export_id)
                    queue_email.assert_awaited()

    expected_key = build_export_storage_key(user_id=user_id, export_id=export_id)
    assert record.status == DataExportStatus.completed
    assert record.storage_key == expected_key
    assert expected_key in storage.uploaded_keys
    assert not temp_file.exists()


@pytest.mark.asyncio
async def test_queue_ready_email_uses_backend_download_url(mock_db):
    export_id = uuid4()
    user = _user()
    record = DataExportRequest(
        id=export_id,
        user_id=user.id,
        status=DataExportStatus.completed,
        storage_key=f"exports/{user.id}/{export_id}.zip",
        completed_at=datetime.now(timezone.utc),
        download_expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db = mock_db(FakeScalarResult(value=user))
    service = DataExportService(storage=FakeSpacesStorage())

    with patch(
        "apps.export.service.email_settings.base_url",
        "https://lynkup-backend-311u.onrender.com",
    ):
        with patch("apps.export.service._queue_email", new=AsyncMock()) as queue_email:
            with patch(
                "apps.export.service._render_email_layout",
                side_effect=lambda title, body_html: body_html,
            ):
                await service._queue_ready_email(db=db, export_request=record)

    args, kwargs = queue_email.await_args
    html_body = args[2]
    download = (
        f"https://lynkup-backend-311u.onrender.com/api/v1/me/export/{export_id}/download"
    )
    assert download in html_body
    assert f"https://lynkup-backend-311u.onrender.com/{export_id}" not in html_body
    assert "attached to this email" not in html_body.lower()
    assert kwargs.get("attachment") is None
    assert "cdn.digitaloceanspaces.com" not in html_body
    assert "kampulynk-dev-spaces.sfo3.digitaloceanspaces.com" not in html_body


@pytest.mark.asyncio
async def test_cleanup_expired_exports_deletes_spaces_object(mock_db):
    export_id = uuid4()
    user_id = uuid4()
    key = f"exports/{user_id}/{export_id}.zip"
    storage = FakeSpacesStorage()
    storage.objects[key] = b"old-zip"
    record = DataExportRequest(
        id=export_id,
        user_id=user_id,
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
    assert key in storage.deleted
    assert key not in storage.objects
