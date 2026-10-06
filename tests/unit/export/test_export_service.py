from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from apps.accounts.db_models import User
from apps.administration.db_models.template_db_model import Template
from apps.administration.initial_templates import INITIAL_TEMPLATES
from apps.export.enums import DataExportStatus
from apps.export.models import DataExportRequest
from apps.export.service import DataExportService, build_export_storage_key
from apps.export.storage import ExportStorage
from common.exceptions import ApiError
from tests.unit.conftest import FakeScalarResult


# ---------------------------------------------------------------------------
# Fake Spaces storage (in-memory, tracks calls)
# ---------------------------------------------------------------------------

class FakeSpacesStorage(ExportStorage):
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.uploaded_keys: list[str] = []
        self._presigned_calls: list[tuple[str, int]] = []

    def upload(self, storage_key: str, data: bytes, content_type: str = "application/zip") -> str:
        self.objects[storage_key] = data
        self.uploaded_keys.append(storage_key)
        return storage_key

    def exists(self, storage_key: str) -> bool:
        return storage_key in self.objects

    def generate_download_url(self, storage_key: str, expires_in: int) -> str:
        self._presigned_calls.append((storage_key, expires_in))
        return f"https://kampulynk-dev-spaces.sfo3.digitaloceanspaces.com/{storage_key}?X-Amz-Signature=test&X-Amz-Expires={expires_in}"

    def delete(self, storage_key: str) -> None:
        self.deleted.append(storage_key)
        self.objects.pop(storage_key, None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _user(user_id=None) -> User:
    return User(
        id=user_id or uuid4(),
        email="owner@example.com",
        role="user",
        firebase_uid="uid-1",
    )


# ---------------------------------------------------------------------------
# request_export
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# get_export_status
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# process_export
# ---------------------------------------------------------------------------

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
    # execute is called multiple times: export_request, profile, university, user (for email)
    session.execute = AsyncMock(side_effect=[
        FakeScalarResult(value=record),    # export request lookup
        FakeScalarResult(value=None),      # profile (None -> X initials)
        FakeScalarResult(value=record),    # refresh after processing commit
        FakeScalarResult(value=None),      # university (skipped since no profile)
        FakeScalarResult(value=None),      # user email lookup
    ])
    session.add = Mock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    temp_file = Path("temp_export_test.zip")
    temp_file.write_bytes(b"PKZIP")

    with patch("apps.export.service.async_session_factory", return_value=session):
        with patch(
            "apps.export.service.DataExportBuilder.build_encrypted_zip_bytes",
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
async def test_process_export_spaces_key_format():
    """Storage key must follow exports/<user_id>/<export_id>.zip format."""
    user_id = uuid4()
    export_id = uuid4()
    key = build_export_storage_key(user_id=user_id, export_id=export_id)
    assert key == f"exports/{user_id}/{export_id}.zip"


@pytest.mark.asyncio
async def test_process_export_presigned_url_uses_7day_ttl():
    """generate_download_url must be called with expires_in=604800."""
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
    session.execute = AsyncMock(side_effect=[
        FakeScalarResult(value=record),
        FakeScalarResult(value=None),  # profile
        FakeScalarResult(value=None),  # user email lookup
    ])
    session.add = Mock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    with patch("apps.export.service.async_session_factory", return_value=session):
        with patch(
            "apps.export.service.DataExportBuilder.build_encrypted_zip_bytes",
            new=AsyncMock(return_value=b"PKZIP"),
        ):
            with patch("apps.export.service.write_temp_zip", return_value=Path("t.zip")):
                with patch.object(Path, "unlink", return_value=None):
                    with patch.object(service, "_queue_ready_email", new=AsyncMock()):
                        await service.process_export(export_id)

    # Verify presigned URL was generated with 7-day TTL
    assert len(storage._presigned_calls) == 1
    _key, ttl = storage._presigned_calls[0]
    assert ttl == 604800, f"Expected 604800s TTL, got {ttl}"


@pytest.mark.asyncio
async def test_process_export_returns_when_missing():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.execute = AsyncMock(return_value=FakeScalarResult(value=None))
    service = DataExportService(storage=FakeSpacesStorage())

    with patch("apps.export.service.async_session_factory", return_value=session):
        await service.process_export(uuid4())

    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_export_skips_non_pending_status():
    export_id = uuid4()
    record = DataExportRequest(
        id=export_id,
        user_id=uuid4(),
        status=DataExportStatus.completed,
    )
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.execute = AsyncMock(return_value=FakeScalarResult(value=record))
    session.commit = AsyncMock()
    service = DataExportService(storage=FakeSpacesStorage())

    with patch("apps.export.service.async_session_factory", return_value=session):
        await service.process_export(export_id)

    session.commit.assert_not_awaited()
    assert record.status == DataExportStatus.completed


@pytest.mark.asyncio
async def test_process_export_marks_failed_on_builder_error():
    export_id = uuid4()
    user_id = uuid4()
    record = DataExportRequest(
        id=export_id,
        user_id=user_id,
        status=DataExportStatus.queued,
    )
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.execute = AsyncMock(side_effect=[
        FakeScalarResult(value=record),
        FakeScalarResult(value=None),  # profile
    ])
    session.add = Mock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    service = DataExportService(storage=FakeSpacesStorage())

    with patch("apps.export.service.async_session_factory", return_value=session):
        with patch(
            "apps.export.service.DataExportBuilder.build_encrypted_zip_bytes",
            new=AsyncMock(side_effect=RuntimeError("zip boom")),
        ):
            await service.process_export(export_id)

    assert record.status == DataExportStatus.failed
    assert record.error_message == "zip boom"


@pytest.mark.asyncio
async def test_process_export_loads_university_for_password():
    export_id = uuid4()
    user_id = uuid4()
    university_id = uuid4()
    record = DataExportRequest(
        id=export_id,
        user_id=user_id,
        status=DataExportStatus.queued,
    )
    profile = Mock(
        first_name="Sam",
        last_name="Lee",
        university_id=university_id,
        major="CS",
        minor="Math",
    )
    university = Mock(name="Test U")
    university.name = "Test U"

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.execute = AsyncMock(side_effect=[
        FakeScalarResult(value=record),
        FakeScalarResult(value=profile),
        FakeScalarResult(value=university),
    ])
    session.add = Mock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    service = DataExportService(storage=FakeSpacesStorage())
    temp_file = Path("temp_export_uni_test.zip")
    temp_file.write_bytes(b"PKZIP")

    with patch("apps.export.service.async_session_factory", return_value=session):
        with patch(
            "apps.export.service.DataExportBuilder.build_encrypted_zip_bytes",
            new=AsyncMock(return_value=b"PKZIP"),
        ) as build_zip:
            with patch("apps.export.service.write_temp_zip", return_value=temp_file):
                with patch.object(service, "_queue_ready_email", new=AsyncMock()):
                    await service.process_export(export_id)

    assert record.status == DataExportStatus.completed
    build_zip.assert_awaited()
    assert not temp_file.exists()


@pytest.mark.asyncio
async def test_cleanup_expired_exports_preserves_failed_objects_for_retry(mock_db):
    export_id = uuid4()
    user_id = uuid4()
    key = f"exports/{user_id}/{export_id}.zip"
    storage = FakeSpacesStorage()
    storage.objects[key] = b"old-zip"

    def _boom(_storage_key: str) -> None:
        raise RuntimeError("spaces down")

    storage.delete = _boom  # type: ignore[method-assign]
    record = DataExportRequest(
        id=export_id,
        user_id=user_id,
        status=DataExportStatus.completed,
        storage_key=key,
        download_expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db = mock_db(FakeScalarResult(values=[record]))
    service = DataExportService(storage=storage)

    with pytest.raises(RuntimeError, match="Failed to clean up"):
        await service.cleanup_expired_exports(db=db)
    assert record.status == DataExportStatus.completed
    assert record.storage_key == key


@pytest.mark.asyncio
async def test_get_export_status_not_found(mock_db):
    user = _user()
    db = mock_db(FakeScalarResult(value=None))
    service = DataExportService(storage=FakeSpacesStorage())

    with pytest.raises(ApiError, match="Export request not found"):
        await service.get_export_status(user=user, export_id=uuid4(), db=db)


def test_get_data_export_service_factory():
    from apps.export.service import get_data_export_service

    service = get_data_export_service()
    assert isinstance(service, DataExportService)


# ---------------------------------------------------------------------------
# _queue_ready_email
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_queue_ready_email_uses_presigned_spaces_url(mock_db):
    export_id = uuid4()
    user = _user()
    expires_at = datetime(2026, 8, 15, 11, 38, 8, 455703, tzinfo=timezone.utc)
    record = DataExportRequest(
        id=export_id,
        user_id=user.id,
        status=DataExportStatus.completed,
        storage_key=f"exports/{user.id}/{export_id}.zip",
        completed_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        download_expires_at=expires_at,
    )
    db = mock_db(FakeScalarResult(value=user))
    service = DataExportService(storage=FakeSpacesStorage())

    fake_presigned_url = (
        "https://kampulynk-dev-spaces.sfo3.digitaloceanspaces.com"
        f"/exports/{user.id}/{export_id}.zip"
        "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=fakesig"
    )
    fake_password = "SD7F2C"

    async def _render_email_by_name(
        user_id,
        template_name,
        context,
        raw_keys=None,
    ):
        assert template_name == "data_export_ready_email"

        download_url = escape(context["download_url"], quote=True)
        zip_password = context["zip_password"]
        download_expires_at = context["download_expires_at"]
        zip_filename = context["zip_filename"]

        return (
            "Your KampuLynk Data Export Is Ready",
            (
                "<!doctype html>"
                "<html>"
                "<body>"
                '<div class="email-container">'
                "<h1>Your KampuLynk Data Export Is Ready</h1>"
                f'<a href="{download_url}">Download Your Data</a>'
                "<div>ZIP Password</div>"
                f"<div>{zip_password}</div>"
                "<div>Your ZIP archive is password-protected. "
                "Use the password above to open the archive.</div>"
                f"<div>Download link expires on: {download_expires_at}</div>"
                f"<div>{zip_filename}</div>"
                "</div>"
                "</body>"
                "</html>"
            ),
        )

    with patch("apps.export.service._queue_email", new=AsyncMock()) as queue_email:
        with patch(
            "apps.export.service.render_email_by_name",
            new=AsyncMock(side_effect=_render_email_by_name),
        ):
            await service._queue_ready_email(
                db=db,
                export_request=record,
                presigned_url=fake_presigned_url,
                zip_password=fake_password,
            )

    args, kwargs = queue_email.await_args
    subject = args[1]
    html_body = args[2]

    assert subject == "Your KampuLynk Data Export Is Ready"
    # CTA href is HTML-escaped so query-string & becomes &amp;.
    escaped_href = fake_presigned_url.replace("&", "&amp;")
    assert f'href="{escaped_href}"' in html_body
    assert "Download Your Data" in html_body
    # Must NOT display the raw URL as visible link text.
    assert f">{fake_presigned_url}<" not in html_body
    # Must contain the 6-character password in a credential-style block.
    assert fake_password in html_body
    assert len(fake_password) == 6
    assert "ZIP Password" in html_body
    assert "Your ZIP archive is password-protected" in html_body
    # Human-readable expiry — never a raw ISO timestamp.
    assert "Download link expires on:" in html_body
    assert "15 August 2026" in html_body
    assert "2026-08-15T11:38:08" not in html_body
    # Subtle filename; export UUID must not be shown as a visible reference.
    assert "kampulynk_data_export_2026-08-10.zip" in html_body
    assert "Export reference" not in html_body
    assert f"Export reference: {export_id}" not in html_body
    # Must NOT contain any /download backend endpoint URL.
    assert f"/api/v1/me/export/{export_id}/download" not in html_body
    # Must NOT use CDN endpoint — Spaces CDN rejects origin SigV4 signatures.
    assert "cdn.digitaloceanspaces.com" not in html_body
    # Must NOT say "attached to this email".
    assert "attached to this email" not in html_body.lower()
    # No ZIP attachment.
    assert kwargs.get("attachment") is None


@pytest.mark.asyncio
async def test_queue_ready_email_does_not_expose_backend_download_url(mock_db):
    """Email link must never be /api/v1/me/export/{id}/download."""
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

    async def _render_email_by_name(
        user_id,
        template_name,
        context,
        raw_keys=None,
    ):
        assert template_name == "data_export_ready_email"

        download_url = escape(context["download_url"], quote=True)
        zip_password = context["zip_password"]

        return (
            "Your KampuLynk Data Export Is Ready",
            (
                "<!doctype html>"
                "<html>"
                "<body>"
                '<div class="email-container">'
                "<h1>Your KampuLynk Data Export Is Ready</h1>"
                f'<a href="{download_url}">Download Your Data</a>'
                "<div>ZIP Password</div>"
                f"<div>{zip_password}</div>"
                "</div>"
                "</body>"
                "</html>"
            ),
        )

    with patch("apps.export.service._queue_email", new=AsyncMock()) as queue_email:
        with patch(
            "apps.export.service.render_email_by_name",
            new=AsyncMock(side_effect=_render_email_by_name),
        ):
            await service._queue_ready_email(
                db=db,
                export_request=record,
                presigned_url="https://kampulynk-dev-spaces.sfo3.digitaloceanspaces.com/exports/test.zip?sig=abc",
                zip_password="AB1234",
            )

    html_body = queue_email.await_args[0][2]
    subject = queue_email.await_args[0][1]
    assert subject == "Your KampuLynk Data Export Is Ready"
    assert 'href="https://kampulynk-dev-spaces.sfo3.digitaloceanspaces.com/exports/test.zip?sig=abc"' in html_body
    assert "Download Your Data" in html_body
    assert ">https://kampulynk-dev-spaces.sfo3.digitaloceanspaces.com/exports/test.zip?sig=abc<" not in html_body
    assert f"/api/v1/me/export/{export_id}/download" not in html_body
    assert "lynkup-backend" not in html_body
    assert "AB1234" in html_body
    assert "Export reference" not in html_body
    assert "AB1234" not in subject


def test_format_export_expiry_date_is_human_readable():
    from apps.export.service import _ensure_aware, _format_export_expiry_date

    expires = datetime(2026, 8, 15, 11, 38, 8, 455703, tzinfo=timezone.utc)
    assert _format_export_expiry_date(expires) == "15 August 2026"
    assert _format_export_expiry_date(None) == "7 days from generation"
    naive = datetime(2026, 1, 5, 12, 0, 0)
    assert _ensure_aware(None) is None
    assert _ensure_aware(naive).tzinfo == timezone.utc
    assert _format_export_expiry_date(naive) == "5 January 2026"


@pytest.mark.asyncio
async def test_data_export_ready_email_template_hides_raw_url():
    from apps.administration.services.template_service import render_email_by_name
    from apps.export.service import _format_export_expiry_date

    url = (
        "https://kampulynk-dev-spaces.sfo3.digitaloceanspaces.com"
        "/exports/u/e.zip?X-Amz-Signature=abc&X-Amz-Expires=604800"
    )
    tpl_data = next(
        template for template in INITIAL_TEMPLATES if template["name"] == "data_export_ready_email"
    )

    class _Session:
        async def execute(self, *_args, **_kwargs):
            return MagicMock(
                scalars=MagicMock(
                    return_value=MagicMock(
                        first=MagicMock(
                            return_value=Template(
                                name=tpl_data["name"],
                                subject=tpl_data["subject"],
                                body_html=tpl_data["body_html"],
                                status="active",
                            )
                        )
                    )
                )
            )

    _, html = await render_email_by_name(
        _Session(),
        "data_export_ready_email",
        {
            "first_name": "User",
            "download_url": url,
            "zip_password": "XY9A1B",
            "zip_filename": "kampulynk_data_export_2026-08-10.zip",
            "download_expires_at": _format_export_expiry_date(
                datetime(2026, 8, 15, tzinfo=timezone.utc)
            ),
        },
        raw_keys={"download_url"},
    )
    assert "Download Your Data" in html
    assert "XY9A1B" in html
    assert "15 August 2026" in html
    assert "kampulynk_data_export_2026-08-10.zip" in html
    assert "/api/v1/me/export/" not in html
    assert "lynkup-backend" not in html


@pytest.mark.asyncio
async def test_queue_ready_email_skips_when_user_missing(mock_db):
    export_id = uuid4()
    user_id = uuid4()
    record = DataExportRequest(
        id=export_id,
        user_id=user_id,
        status=DataExportStatus.completed,
        storage_key=f"exports/{user_id}/{export_id}.zip",
        completed_at=datetime.now(timezone.utc),
        download_expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db = mock_db(FakeScalarResult(value=None))
    service = DataExportService(storage=FakeSpacesStorage())

    with patch("apps.export.service._queue_email", new=AsyncMock()) as queue_email:
        await service._queue_ready_email(
            db=db,
            export_request=record,
            presigned_url="https://spaces.example.com/exports/test.zip?sig=abc",
            zip_password="AB1234",
        )

    queue_email.assert_not_awaited()


# ---------------------------------------------------------------------------
# cleanup_expired_exports
# ---------------------------------------------------------------------------

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


def test_export_email_purpose_disables_sendgrid_click_tracking():
    from core.email_service import (
        _NO_CLICK_TRACKING_PURPOSES,
        _disable_sendgrid_click_tracking,
    )

    assert "Data Export Ready" in _NO_CLICK_TRACKING_PURPOSES
    message = type("Mail", (), {})()
    _disable_sendgrid_click_tracking(message)
    click = message.tracking_settings.click_tracking
    assert click.enable is False
    assert click.enable_text is False
