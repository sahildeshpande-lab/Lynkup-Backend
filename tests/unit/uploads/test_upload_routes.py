from __future__ import annotations

import io
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from apps.uploads import routes as upload_routes
from core.security.auth import get_current_user_moderator_or_superadmin
from entrypoints.api import app


client = TestClient(app)


def _override_upload_user():
    return SimpleNamespace(id=uuid4(), role="user")


def setup_module() -> None:
    app.dependency_overrides[get_current_user_moderator_or_superadmin] = _override_upload_user


def teardown_module() -> None:
    app.dependency_overrides.pop(get_current_user_moderator_or_superadmin, None)


def test_upload_image_success(monkeypatch) -> None:
    async def _fake_upload_profile(*, file, user_id):
        return {
            "status": True,
            "message": "image uploaded",
            "data": {"key": f"profiles/{user_id}.png", "url": f"https://cdn.example/profiles/{user_id}.png"},
        }

    monkeypatch.setattr(upload_routes.storage_service, "upload_profile", _fake_upload_profile)

    response = client.post(
        "/api/v1/uploads/image",
        data={"prefix": "profiles"},
        files={"file": ("avatar.png", io.BytesIO(b"fake-image"), "image/png")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["data"]["key"].startswith("profiles/")
    assert body["data"]["url"].startswith("https://cdn.example/")


def test_upload_image_requires_auth() -> None:
    app.dependency_overrides.pop(get_current_user_moderator_or_superadmin, None)
    try:
        response = client.post(
            "/api/v1/uploads/image",
            data={"prefix": "profiles"},
            files={"file": ("avatar.png", io.BytesIO(b"fake-image"), "image/png")},
        )
        assert response.status_code in (401, 403)
    finally:
        app.dependency_overrides[get_current_user_moderator_or_superadmin] = _override_upload_user


def test_upload_image_rejects_invalid_prefix() -> None:
    response = client.post(
        "/api/v1/uploads/image",
        data={"prefix": "posts"},
        files={"file": ("avatar.png", io.BytesIO(b"fake-image"), "image/png")},
    )

    assert response.status_code == 400


def test_upload_image_rejects_invalid_content_type(monkeypatch) -> None:
    from fastapi import HTTPException

    async def _reject(*, file, user_id):
        raise HTTPException(status_code=400, detail="Unsupported file type")

    monkeypatch.setattr(upload_routes.storage_service, "upload_profile", _reject)

    response = client.post(
        "/api/v1/uploads/image",
        data={"prefix": "profiles"},
        files={"file": ("doc.pdf", io.BytesIO(b"fake-pdf"), "application/pdf")},
    )

    assert response.status_code == 400
