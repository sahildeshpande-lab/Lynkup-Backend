from __future__ import annotations

import io

from fastapi.testclient import TestClient

from apps.uploads import routes as upload_routes
from entrypoints.api import app


client = TestClient(app)


def test_upload_image_success(monkeypatch) -> None:
    monkeypatch.setattr(upload_routes, "save_image", lambda **kwargs: None)
    monkeypatch.setattr(upload_routes, "generate_download_url", lambda key: f"https://cdn.example/{key}")

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


def test_upload_image_rejects_invalid_prefix() -> None:
    response = client.post(
        "/api/v1/uploads/image",
        data={"prefix": "posts"},
        files={"file": ("avatar.png", io.BytesIO(b"fake-image"), "image/png")},
    )

    assert response.status_code == 200
    assert response.json()["status"] is False


def test_upload_image_rejects_invalid_content_type() -> None:
    response = client.post(
        "/api/v1/uploads/image",
        data={"prefix": "profiles"},
        files={"file": ("doc.pdf", io.BytesIO(b"fake-pdf"), "application/pdf")},
    )

    assert response.status_code == 200
    assert response.json()["status"] is False


def test_upload_image_rejects_oversized_file() -> None:
    oversized = b"x" * (5 * 1024 * 1024 + 1)
    response = client.post(
        "/api/v1/uploads/image",
        data={"prefix": "banners"},
        files={"file": ("banner.png", io.BytesIO(oversized), "image/png")},
    )

    assert response.status_code == 200
    assert response.json()["status"] is False
