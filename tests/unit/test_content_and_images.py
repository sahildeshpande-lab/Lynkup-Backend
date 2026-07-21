from __future__ import annotations

from types import SimpleNamespace
import importlib

import pytest
from botocore.exceptions import ClientError
from fastapi import HTTPException

from apps.feed import content_utils
from core.images import config as image_config

storage_service = importlib.import_module("core.images.storage_service")


def test_content_utils_sanitize_normalize_extract_and_validate():
    html = '<p>Hello <script>bad()</script><b>#AI</b></p><p>#AI #Campus</p>'

    sanitized = content_utils.sanitize_html(html)
    assert "<script>" not in sanitized
    assert content_utils.normalize_text("<p>Hello</p><p> world </p>") == "Hello world"
    assert content_utils.extract_hashtags(caption="#AI", content_html=html) == ["ai", "campus"]

    content_utils.validate_hashtag_count("#one #two", None, limit=2)
    with pytest.raises(ValueError, match="Maximum 1 hashtags"):
        content_utils.validate_hashtag_count("#one #two", None, limit=1)

    content_utils.validate_content({"caption": "A post", "visibility": "hidden"})
    # Caption is optional — blank captions are allowed.
    content_utils.validate_content({"caption": "   "})
    with pytest.raises(ValueError, match="Invalid visibility"):
        content_utils.validate_content({"caption": "x", "visibility": "friends"})
    with pytest.raises(ValueError, match="Content exceeds"):
        content_utils.validate_content({"caption": "x", "content_html": "x" * 5001})

    content_utils.validate_media_count([1, 2, 3, 4, 5])
    with pytest.raises(ValueError, match="Maximum 5 media"):
        content_utils.validate_media_count([1, 2, 3, 4, 5, 6])


def test_validate_media_asset_document_and_audio_rules():
    document = SimpleNamespace(mime_type="application/pdf", file_size=1024)
    audio = SimpleNamespace(mime_type="audio/mpeg", file_size=1024)
    image = SimpleNamespace(mime_type="image/png", file_size=100)

    content_utils.validate_media_asset(document, "document")
    content_utils.validate_media_asset(audio, "audio")
    content_utils.validate_media_asset(image, "image")

    with pytest.raises(ValueError, match="Unsupported document"):
        content_utils.validate_media_asset(SimpleNamespace(mime_type="text/plain", file_size=1), "document")
    with pytest.raises(ValueError, match="20 MB"):
        content_utils.validate_media_asset(
            SimpleNamespace(mime_type="application/pdf", file_size=content_utils.MAX_DOCUMENT_SIZE + 1),
            "document",
        )
    with pytest.raises(ValueError, match="Unsupported audio"):
        content_utils.validate_media_asset(SimpleNamespace(mime_type="video/mp4", file_size=1), "audio")
    with pytest.raises(ValueError, match="25 MB"):
        content_utils.validate_media_asset(
            SimpleNamespace(mime_type="audio/mpeg", file_size=content_utils.MAX_AUDIO_SIZE + 1),
            "audio",
        )


def test_image_settings_and_url_helpers(monkeypatch):
    settings = image_config.ImageStorageSettings(
        S3_BUCKET=None,
        AWS_S3_BUCKET="legacy",
        S3_ACCESS_KEY=None,
        AWS_ACCESS_KEY_ID="access",
        S3_SECRET_KEY=None,
        AWS_SECRET_ACCESS_KEY="secret",
        BASE_URL="https://api.example.test",
    )
    assert settings.effective_bucket == "legacy"
    assert settings.effective_access_key == "access"
    assert settings.effective_secret_key == "secret"

    monkeypatch.setattr(image_config.settings, "S3_FILE_ENDPOINT", "https://files.example.test/")
    monkeypatch.setattr(image_config.settings, "S3_CDN_ENDPOINT", None)
    assert storage_service.get_media_url("profiles/me.png") == "https://files.example.test/profiles/me.png"
    assert storage_service.get_media_url("https://cdn/x.png") == "https://cdn/x.png"
    assert storage_service.get_media_url("") == ""

    monkeypatch.setattr(image_config.settings, "S3_FILE_ENDPOINT", None)
    monkeypatch.setattr(image_config.settings, "S3_CDN_ENDPOINT", None)
    assert storage_service.get_media_url("/profiles/me.png") == "/profiles/me.png"


def test_image_validation_extensions_and_uploads(monkeypatch):
    storage_service.validate_image(b"abc", "image/png")
    assert storage_service.get_extension("image/jpeg") == "jpg"
    assert storage_service.get_extension("", "photo.jpeg") == "jpg"
    assert storage_service.get_extension("", "photo.unknown") == "png"

    with pytest.raises(HTTPException, match="400"):
        storage_service.validate_image(b"", "image/png")
    with pytest.raises(HTTPException):
        storage_service.validate_image(b"abc", "text/plain")
    with pytest.raises(HTTPException):
        storage_service.validate_image(b"x" * (storage_service.MAX_IMAGE_SIZE + 1), "image/png")

    uploads = []
    monkeypatch.setattr(storage_service.StorageService, "upload_file", lambda **kwargs: uploads.append(kwargs) or kwargs["key"])
    monkeypatch.setattr(storage_service, "get_media_url", lambda key: f"https://cdn.example.test/{key}")

    async def run_uploads():
        banner = await storage_service.StorageService.upload_banner(
            b"png-bytes", "banner-id", content_type="image/png"
        )
        profile = await storage_service.StorageService.upload_profile(
            b"jpg-bytes", "user-id", content_type="image/jpeg"
        )
        post = await storage_service.StorageService.upload_post_media(
            b"%PDF", "post-id", content_type="application/pdf", filename="paper.pdf"
        )
        return banner, profile, post

    import anyio

    banner, profile, post = anyio.run(run_uploads)
    assert banner["data"]["key"] == "banners/banner-id.png"
    assert profile["data"]["key"] == "profiles/user-id.jpg"
    assert post["data"]["key"] == "posts/post-id.pdf"
    assert [item["key"] for item in uploads] == [
        "banners/banner-id.png",
        "profiles/user-id.jpg",
        "posts/post-id.pdf",
    ]


def test_image_config_download_upload_delete_paths(monkeypatch):
    monkeypatch.setattr(image_config.settings, "S3_BUCKET", None)
    monkeypatch.setattr(image_config.settings, "aws_s3_bucket", None)
    monkeypatch.setattr(image_config.settings, "base_url_img", "https://img.example.test/")
    monkeypatch.setattr(image_config.settings, "base_url", None)
    monkeypatch.setattr(image_config.settings, "S3_FILE_ENDPOINT", None)

    assert image_config.build_image_key("avatar.png") == "images/avatar.png"
    assert image_config.normalize_image_name(" avatar.png ") == "avatar.png"
    assert image_config.generate_upload_url("avatar.png") == ""
    assert image_config.generate_download_url("avatar.png") == "https://img.example.test/static/uploads/avatar.png"
    assert image_config.generate_download_url("https://already.test/a.png") == "https://already.test/a.png"
    assert image_config.generate_download_url("") == ""

    monkeypatch.setattr(image_config.settings, "base_url_img", None)
    monkeypatch.setattr(image_config.settings, "base_url", "https://api.example.test/")
    assert image_config.generate_download_url("avatar.png") == "https://api.example.test/static/uploads/avatar.png"

    monkeypatch.setattr(image_config.settings, "base_url", None)
    monkeypatch.setattr(image_config.settings, "S3_FILE_ENDPOINT", "https://files.example.test/")
    assert image_config.generate_download_url("avatar.png") == "https://files.example.test/avatar.png"

    monkeypatch.setattr(image_config.settings, "S3_BUCKET", "bucket")
    monkeypatch.setattr(image_config.settings, "S3_FILE_ENDPOINT", None)
    monkeypatch.setattr(
        image_config.s3_client,
        "generate_presigned_url",
        lambda operation, Params, ExpiresIn: f"{operation}:{Params['Key']}:{ExpiresIn}",
    )
    assert image_config.generate_upload_url("avatar.png", 9) == "put_object:avatar.png:9"
    assert image_config.generate_download_url("avatar.png", 7) == "get_object:avatar.png:7"

    error = ClientError({"Error": {"Code": "404"}}, "HeadObject")
    monkeypatch.setattr(image_config.s3_client, "head_object", lambda **kwargs: (_ for _ in ()).throw(error))
    assert image_config.file_exists("missing.png") is False

    deleted = []
    monkeypatch.setattr(image_config.s3_client, "delete_object", lambda **kwargs: deleted.append(kwargs))
    image_config.delete_file("avatar.png")
    assert deleted == [{"Bucket": "bucket", "Key": "avatar.png"}]

    monkeypatch.setattr(image_config.s3_client, "head_object", lambda **kwargs: None)
    assert image_config.file_exists("exists.png") is True


def test_storage_service_delete_and_upload_file_s3_branches(monkeypatch):
    monkeypatch.setattr(image_config.settings, "S3_BUCKET", "bucket")
    monkeypatch.setattr(image_config.settings, "aws_s3_bucket", None)
    calls = []
    monkeypatch.setattr(storage_service.config.s3_client, "delete_object", lambda **kwargs: calls.append(("delete", kwargs)))
    monkeypatch.setattr(storage_service.config.s3_client, "put_object", lambda **kwargs: calls.append(("put", kwargs)))

    storage_service.delete_file("profiles/a.png")
    key = storage_service.StorageService.upload_file(b"abc", "profiles/a.png", "image/png")

    assert key == "profiles/a.png"
    assert calls[0] == ("delete", {"Bucket": "bucket", "Key": "profiles/a.png"})
    assert calls[1][0] == "put"
    assert calls[1][1]["ACL"] == "public-read"

    storage_service.delete_file("")
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_upload_image_to_s3_http_base64_and_passthrough(monkeypatch):
    import httpx
    import uuid as uuid_module

    saved = []
    monkeypatch.setattr(image_config, "save_image", lambda file_name, content, content_type: saved.append((file_name, content, content_type)))
    monkeypatch.setattr(image_config, "generate_download_url", lambda file_name: f"https://cdn.example.test/{file_name}")
    monkeypatch.setattr(uuid_module, "uuid4", lambda: "fixed-id")
    monkeypatch.setattr(image_config.settings, "aws_s3_bucket", "bucket-name")

    assert await image_config.upload_image_to_s3("https://bucket-name/path.png") == "https://bucket-name/path.png"

    class FakeResponse:
        status_code = 200
        content = b"image-bytes"
        headers = {"content-type": "image/jpeg; charset=binary"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, timeout):
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    assert await image_config.upload_image_to_s3("https://remote.example.test/avatar", prefix="profiles") == (
        "https://cdn.example.test/profiles/fixed-id.jpeg"
    )
    assert saved[-1] == ("profiles/fixed-id.jpeg", b"image-bytes", "image/jpeg; charset=binary")

    data_url = "data:image/png;base64,YWJj"
    assert await image_config.upload_image_to_s3(data_url, prefix="profiles") == (
        "https://cdn.example.test/profiles/fixed-id.png"
    )
    assert saved[-1] == ("profiles/fixed-id.png", b"abc", "image/png")

    assert await image_config.upload_image_to_s3(" plain-key ") == "plain-key"
    assert await image_config.upload_image_to_s3("") == ""


@pytest.mark.asyncio
async def test_upload_image_to_s3_error_paths(monkeypatch):
    import httpx

    class FailingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, timeout):
            raise RuntimeError("network down")

    monkeypatch.setattr(httpx, "AsyncClient", FailingClient)
    assert await image_config.upload_image_to_s3("https://remote.example.test/avatar") == "https://remote.example.test/avatar"
    assert await image_config.upload_image_to_s3("data:image/png;base64,%%%") == "data:image/png;base64,%%%"


def test_image_config_local_storage_paths(monkeypatch):
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        monkeypatch.setattr(image_config.settings, "S3_BUCKET", None)
        monkeypatch.setattr(image_config.settings, "aws_s3_bucket", None)
        monkeypatch.setattr(image_config.settings, "base_url", None)
        monkeypatch.setattr(image_config.settings, "base_url_img", None)
        monkeypatch.setattr(image_config.settings, "S3_FILE_ENDPOINT", None)

        fake_module = tmp_path / "core" / "images" / "config.py"
        fake_module.parent.mkdir(parents=True)
        fake_module.write_text("")
        monkeypatch.setattr(image_config, "__file__", str(fake_module))

        image_config.save_image("profiles/test.png", b"local-bytes", "image/png")
        target = tmp_path / "entrypoints" / "static" / "uploads" / "profiles" / "test.png"
        assert target.read_bytes() == b"local-bytes"
        assert image_config.file_exists("profiles/test.png") is True
        assert image_config.generate_download_url("profiles/test.png") == "/static/uploads/profiles/test.png"
        assert image_config.generate_download_url("/static/uploads/profiles/test.png") == "/static/uploads/profiles/test.png"

        image_config.delete_file("profiles/test.png")
        assert not target.exists()
        assert image_config.file_exists("") is False
        image_config.delete_file("")
