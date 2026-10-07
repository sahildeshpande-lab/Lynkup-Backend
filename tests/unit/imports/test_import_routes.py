from __future__ import annotations

from fastapi.testclient import TestClient

from apps.accounts.db_models import User
from apps.imports import routes as import_routes
from core.database.session import get_session
from core.security.auth import get_current_admin
from entrypoints.api import app
from tests.unit.imports.conftest import csv_bytes

client = TestClient(app)


class _NoopSession:
    async def execute(self, *_args, **_kwargs):
        raise AssertionError("db session should not be used in this route test")

    async def commit(self):
        return None

    async def rollback(self):
        return None


async def _override_session():
    yield _NoopSession()


async def _override_admin():
    user = User(
        id="11111111-1111-1111-1111-111111111111",
        email="admin@example.com",
        firebase_uid="admin-uid",
    )
    user.role = "superadmin"
    return user


def setup_module() -> None:
    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_admin] = _override_admin


def teardown_module() -> None:
    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(get_current_admin, None)


def test_admin_import_route_success(monkeypatch) -> None:
    async def _import_upload(**kwargs) -> dict:
        assert kwargs["import_type"].value == "major"
        assert kwargs["file"].filename == "majors.csv"
        return {
            "type": "major",
            "fileName": "majors.csv",
            "totalRecords": 3,
            "successfulCount": 2,
            "duplicateCount": 1,
            "failedCount": 0,
            "failedRows": [],
            "duplicateRows": [{"row": 3, "reason": "Major already exists"}],
        }

    monkeypatch.setattr(import_routes, "import_upload", _import_upload)
    response = client.post(
        "/api/v1/admin/import",
        data={"type": "major"},
        files={"file": ("majors.csv", csv_bytes(["name"], [["A"]]), "text/csv")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Import completed"
    assert body["data"]["successfulCount"] == 2
    assert body["data"]["duplicateCount"] == 1


def test_admin_import_route_partial_failures(monkeypatch) -> None:
    async def _import_upload(**kwargs) -> dict:
        return {
            "type": "major",
            "fileName": "majors.xlsx",
            "totalRecords": 100,
            "successfulCount": 85,
            "duplicateCount": 10,
            "failedCount": 5,
            "failedRows": [{"row": 12, "reason": "Name is required"}],
            "duplicateRows": [{"row": 18, "reason": "Major already exists"}],
        }

    monkeypatch.setattr(import_routes, "import_upload", _import_upload)
    response = client.post(
        "/api/v1/admin/import",
        data={"type": "major"},
        files={"file": ("majors.xlsx", b"fake", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Import completed with some failures"
    assert body["data"]["totalRecords"] == 100


def test_admin_import_rejects_invalid_type() -> None:
    response = client.post(
        "/api/v1/admin/import",
        data={"type": "unknown"},
        files={"file": ("majors.csv", b"name\nA\n", "text/csv")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] is False


def test_admin_import_type_is_form_not_query(monkeypatch) -> None:
    async def _import_upload(**kwargs) -> dict:
        assert kwargs["import_type"].value == "country"
        return {
            "type": "country",
            "fileName": "countries.csv",
            "totalRecords": 1,
            "successfulCount": 1,
            "duplicateCount": 0,
            "failedCount": 0,
            "failedRows": [],
            "duplicateRows": [],
        }

    monkeypatch.setattr(import_routes, "import_upload", _import_upload)
    response = client.post(
        "/api/v1/admin/import?type=major",
        data={"type": "country"},
        files={"file": ("countries.csv", b"name,code\nIndia,IN\n", "text/csv")},
    )
    assert response.status_code == 200
    assert response.json()["data"]["type"] == "country"
