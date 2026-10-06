"""Tests for post media upload filename persistence."""

from __future__ import annotations

import io
import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import UploadFile

from apps.feed.services.media_service import upload_post_media_service


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("upload_name", "expected_name"),
    [
        ("Java notes.pdf", "Java notes.pdf"),
        ("Java%20notes.pdf", "Java notes.pdf"),
        (
            "Star%20Pattern%20Programs%20in%20Java%20%F0%9F%92%A1.pdf",
            "Star Pattern Programs in Java 💡.pdf",
        ),
    ],
)
async def test_upload_document_persists_decoded_original_filename(
    mock_db,
    monkeypatch,
    upload_name: str,
    expected_name: str,
) -> None:
    captured: dict = {}

    async def fake_upload_post_media(**kwargs):
        captured["filename"] = kwargs.get("filename")
        captured["file_uuid"] = kwargs.get("file_uuid")
        return {"data": {"key": f"posts/{kwargs['file_uuid']}.pdf"}}

    monkeypatch.setattr(
        "apps.feed.services.media_service.storage_service.upload_post_media",
        fake_upload_post_media,
    )
    monkeypatch.setattr(
        "apps.feed.services.media_service.generate_download_url",
        lambda key: f"https://cdn.example.test/{key}",
    )

    file = UploadFile(
        filename=upload_name,
        file=io.BytesIO(b"%PDF-1.4 fake document"),
        headers={"content-type": "application/pdf"},
    )
    db = mock_db()
    db.refresh = AsyncMock()

    result = await upload_post_media_service(
        user_id=uuid.uuid4(),
        files=[file],
        db=db,
    )

    assert len(result) == 1
    item = result[0]
    assert item["original_filename"] == expected_name
    assert item["type"] == "document"
    assert item["key"] == f"posts/{captured['file_uuid']}.pdf"
    assert item["key"].startswith("posts/")
    assert item["key"].endswith(".pdf")
    assert expected_name not in item["key"]
    assert captured["filename"] == upload_name

    saved_asset = db.add.call_args[0][0]
    assert saved_asset.original_filename == expected_name
    assert saved_asset.key == item["key"]
    assert saved_asset.key == f"posts/{captured['file_uuid']}.pdf"
