from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from apps.accounts.db_models import User
from apps.export.enums import DataExportStatus
from apps.export.schemas import ExportRequestAcceptedData, ExportStatusData
from apps.export.service import DataExportService, get_data_export_service
from common.exceptions import ApiError
from core.auth.dependencies import require_recent_auth
from core.database.session import get_session
from core.security.auth import get_current_admin, get_current_user
from entrypoints.api import app
from tests.unit.conftest import FakeScalarResult

client = TestClient(app)

USER_ID = uuid4()
ADMIN_ID = uuid4()


async def _override_current_user():
    return User(id=USER_ID, email="jane@example.com", role="user", firebase_uid="test-uid")


async def _override_admin():
    return User(id=ADMIN_ID, email="admin@example.com", role="admin", firebase_uid="admin-uid")


async def _override_recent_auth():
    return {"uid": "test-uid", "auth_time": int(datetime.now(timezone.utc).timestamp())}


class _Session:
    async def execute(self, *_args, **_kwargs):
        return FakeScalarResult()

    def add(self, obj):
        return None

    async def commit(self):
        return None

    async def refresh(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid4()
        return None


def setup_module() -> None:
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_current_admin] = _override_admin
    app.dependency_overrides[require_recent_auth] = _override_recent_auth

    async def _override_session():
        yield _Session()

    app.dependency_overrides[get_session] = _override_session


def teardown_module() -> None:
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_current_admin, None)
    app.dependency_overrides.pop(require_recent_auth, None)
    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(get_data_export_service, None)


def test_request_export_requires_auth() -> None:
    override = app.dependency_overrides.pop(get_current_user, None)
    try:
        response = client.post("/api/v1/me/export")
        assert response.status_code in (401, 200)
        body = response.json()
        assert body["status"] is False
    finally:
        if override:
            app.dependency_overrides[get_current_user] = override


def test_authenticated_user_can_request_export(monkeypatch) -> None:
    accepted = ExportRequestAcceptedData(
        export_id=uuid4(),
        status=DataExportStatus.queued,
    )
    enqueued: list = []

    async def _request_export(*, user, db):
        assert user.id == USER_ID
        return accepted

    def _enqueue(export_id):
        enqueued.append(export_id)
        return "celery-task-1"

    service = DataExportService()
    monkeypatch.setattr(service, "request_export", _request_export)
    monkeypatch.setattr("apps.export.router.enqueue_export_processing", _enqueue)
    app.dependency_overrides[get_data_export_service] = lambda: service

    response = client.post(
        "/api/v1/me/export",
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["data"]["status"] == "queued"
    assert enqueued == [accepted.export_id]


def test_request_export_accepted_when_celery_enqueue_fails(monkeypatch) -> None:
    accepted = ExportRequestAcceptedData(
        export_id=uuid4(),
        status=DataExportStatus.queued,
    )

    async def _request_export(*, user, db):
        return accepted

    def _enqueue(_export_id):
        raise RuntimeError("broker down")

    service = DataExportService()
    monkeypatch.setattr(service, "request_export", _request_export)
    monkeypatch.setattr("apps.export.router.enqueue_export_processing", _enqueue)
    app.dependency_overrides[get_data_export_service] = lambda: service

    response = client.post(
        "/api/v1/me/export",
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["data"]["status"] == "queued"
    assert body["data"]["export_id"] == str(accepted.export_id)


def test_user_cannot_access_another_users_export(monkeypatch) -> None:
    service = DataExportService()

    async def _status(*, user, export_id, db):
        raise ApiError("Export request not found.")

    monkeypatch.setattr(service, "get_export_status", _status)
    app.dependency_overrides[get_data_export_service] = lambda: service

    response = client.get(
        f"/api/v1/me/export/{uuid4()}",
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code in (200, 401)
    body = response.json()
    assert body["status"] is False


def test_get_export_status_success(monkeypatch) -> None:
    export_id = uuid4()
    payload = ExportStatusData(
        id=export_id,
        status=DataExportStatus.processing,
        requested_at=datetime.now(timezone.utc),
    )
    service = DataExportService()

    async def _status(*, user, export_id, db):
        return payload

    monkeypatch.setattr(service, "get_export_status", _status)
    app.dependency_overrides[get_data_export_service] = lambda: service

    response = client.get(
        f"/api/v1/me/export/{export_id}",
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["status"] == "processing"
    assert "storage_key" not in body["data"]


def test_download_endpoint_does_not_exist() -> None:
    """GET /api/v1/me/export/{id}/download must return 404 — the route was removed."""
    response = client.get(
        f"/api/v1/me/export/{uuid4()}/download",
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 404


def test_admin_cleanup_expired_exports_success(monkeypatch) -> None:
    async def _cleanup(name, queue):
        assert name == "kampulynk.export.cleanup"
        return "cleanup-task-id"

    monkeypatch.setattr("core.jobs.publishing.publish_task", _cleanup)

    response = client.post(
        "/api/v1/admin/exports/cleanup",
        headers={"Authorization": "Bearer admin-token"},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] is True
    assert body["data"] == {"task_id": "cleanup-task-id", "status": "queued"}


def test_admin_cleanup_expired_exports_failure(monkeypatch) -> None:
    async def _failing_cleanup(name, queue):
        raise RuntimeError("broker down")

    monkeypatch.setattr("core.jobs.publishing.publish_task", _failing_cleanup)

    response = client.post(
        "/api/v1/admin/exports/cleanup",
        headers={"Authorization": "Bearer admin-token"},
    )
    assert response.status_code == 503
    body = response.json()
    assert body["status"] is False
    assert "unable to queue" in body["message"].lower()


def test_admin_cleanup_requires_admin() -> None:
    override = app.dependency_overrides.pop(get_current_admin, None)
    try:
        response = client.post("/api/v1/admin/exports/cleanup")
        assert response.status_code in (200, 401)
        body = response.json()
        assert body["status"] is False
    finally:
        if override:
            app.dependency_overrides[get_current_admin] = override
