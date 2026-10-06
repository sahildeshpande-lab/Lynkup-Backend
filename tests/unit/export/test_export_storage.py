from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from apps.export.storage import (
    DigitalOceanSpacesExportStorage,
    _boto_endpoint_url_for_origin,
    _ensure_https,
    _normalize_key,
)


def test_normalize_key_rejects_traversal():
    with pytest.raises(ValueError):
        _normalize_key("../evil.zip")


def test_spaces_upload_uses_private_put_object_without_acl():
    client = MagicMock()
    storage = DigitalOceanSpacesExportStorage()
    storage._client = client
    storage._settings = MagicMock(effective_bucket="kampulynk-dev-spaces")

    key = storage.upload("exports/user/export.zip", b"PKZIP")
    assert key == "exports/user/export.zip"
    kwargs = client.put_object.call_args.kwargs
    assert kwargs["Bucket"] == "kampulynk-dev-spaces"
    assert kwargs["Key"] == "exports/user/export.zip"
    assert kwargs["Body"] == b"PKZIP"
    assert "ACL" not in kwargs


def test_spaces_exists_handles_missing():
    client = MagicMock()
    error = ClientError(
        {"Error": {"Code": "404", "Message": "Not Found"}},
        "HeadObject",
    )
    client.head_object.side_effect = error
    storage = DigitalOceanSpacesExportStorage()
    storage._client = client
    storage._settings = MagicMock(effective_bucket="bucket")
    assert storage.exists("exports/u/e.zip") is False


def test_spaces_generate_download_url_is_presigned_origin():
    client = MagicMock()
    client.generate_presigned_url.return_value = (
        "https://kampulynk-dev-spaces.sfo3.digitaloceanspaces.com/exports/u/e.zip"
        "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=abc"
    )
    storage = DigitalOceanSpacesExportStorage()
    storage._origin_presign_client = client
    storage._settings = MagicMock(
        effective_bucket="kampulynk-dev-spaces",
        S3_ENDPOINT="https://sfo3.digitaloceanspaces.com",
    )

    url = storage.generate_download_url("exports/u/e.zip", expires_in=600)
    assert "X-Amz-Signature" in url
    assert "cdn.digitaloceanspaces.com" not in url
    assert url.startswith("https://")
    client.generate_presigned_url.assert_called_once()
    call_kwargs = client.generate_presigned_url.call_args
    assert call_kwargs.args[0] == "get_object"
    assert call_kwargs.kwargs["ExpiresIn"] == 600


def test_spaces_generate_download_url_does_not_use_shared_origin_client():
    shared = MagicMock()
    presign = MagicMock()
    presign.generate_presigned_url.return_value = (
        "https://kampulynk-dev-spaces.sfo3.digitaloceanspaces.com/exports/u/e.zip"
        "?X-Amz-Signature=abc"
    )
    storage = DigitalOceanSpacesExportStorage()
    storage._client = shared
    storage._origin_presign_client = presign
    storage._settings = MagicMock(
        effective_bucket="kampulynk-dev-spaces",
        S3_ENDPOINT="https://sfo3.digitaloceanspaces.com",
    )
    storage.generate_download_url("exports/u/e.zip", expires_in=600)
    presign.generate_presigned_url.assert_called_once()
    shared.generate_presigned_url.assert_not_called()


def test_spaces_generate_download_url_falls_back_without_s3_endpoint():
    origin = MagicMock()
    origin.generate_presigned_url.return_value = (
        "http://kampulynk-dev-spaces.sfo3.digitaloceanspaces.com/exports/u/e.zip"
        "?X-Amz-Signature=abc"
    )
    storage = DigitalOceanSpacesExportStorage()
    storage._client = origin
    storage._settings = MagicMock(
        effective_bucket="kampulynk-dev-spaces",
        S3_ENDPOINT="",
    )
    url = storage.generate_download_url("exports/u/e.zip", expires_in=600)
    assert url.startswith("https://")
    origin.generate_presigned_url.assert_called_once()


def test_spaces_delete_calls_delete_object():
    client = MagicMock()
    storage = DigitalOceanSpacesExportStorage()
    storage._client = client
    storage._settings = MagicMock(effective_bucket="bucket")
    storage.delete("exports/u/e.zip")
    client.delete_object.assert_called_once_with(
        Bucket="bucket",
        Key="exports/u/e.zip",
    )


def test_spaces_generate_download_url_7day_expiry():
    """service.py passes expires_in=604800 (7 days); verify it flows through."""
    client = MagicMock()
    client.generate_presigned_url.return_value = (
        "https://kampulynk-dev-spaces.sfo3.digitaloceanspaces.com/exports/u/e.zip"
        "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=abc"
    )
    storage = DigitalOceanSpacesExportStorage()
    storage._origin_presign_client = client
    storage._settings = MagicMock(
        effective_bucket="kampulynk-dev-spaces",
        S3_ENDPOINT="https://sfo3.digitaloceanspaces.com",
    )
    storage.generate_download_url("exports/u/e.zip", expires_in=604800)
    call_kwargs = client.generate_presigned_url.call_args
    assert call_kwargs.kwargs["ExpiresIn"] == 604800


def test_presign_client_builds_origin_client_with_virtual_addressing(monkeypatch):
    captured: dict = {}

    def fake_client(service_name: str, **kwargs):
        captured["service_name"] = service_name
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr("apps.export.storage.boto3.client", fake_client)
    storage = DigitalOceanSpacesExportStorage()
    storage._origin_presign_client = None
    storage._settings = MagicMock(
        effective_bucket="kampulynk-dev-spaces",
        S3_ENDPOINT="https://sfo3.digitaloceanspaces.com",
        effective_access_key="key",
        effective_secret_key="secret",
    )
    client = storage._presign_client()
    assert client is not None
    assert captured["service_name"] == "s3"
    assert captured["endpoint_url"] == "https://sfo3.digitaloceanspaces.com"
    assert captured["config"].s3["addressing_style"] == "virtual"


def test_boto_endpoint_url_for_origin_strips_bucket_subdomain():
    assert (
        _boto_endpoint_url_for_origin(
            "https://kampulynk-dev-spaces.sfo3.digitaloceanspaces.com",
            "kampulynk-dev-spaces",
        )
        == "https://sfo3.digitaloceanspaces.com"
    )


def test_boto_endpoint_url_for_origin_keeps_region_host():
    assert (
        _boto_endpoint_url_for_origin(
            "https://sfo3.digitaloceanspaces.com",
            "kampulynk-dev-spaces",
        )
        == "https://sfo3.digitaloceanspaces.com"
    )


def test_boto_endpoint_url_for_origin_upgrades_http():
    assert (
        _boto_endpoint_url_for_origin(
            "http://sfo3.digitaloceanspaces.com",
            "kampulynk-dev-spaces",
        )
        == "https://sfo3.digitaloceanspaces.com"
    )


def test_presign_client_upgrades_http_s3_endpoint(monkeypatch):
    captured: dict = {}

    def fake_client(service_name: str, **kwargs):
        captured["service_name"] = service_name
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr("apps.export.storage.boto3.client", fake_client)
    storage = DigitalOceanSpacesExportStorage()
    storage._origin_presign_client = None
    storage._settings = MagicMock(
        effective_bucket="kampulynk-dev-spaces",
        S3_ENDPOINT="http://sfo3.digitaloceanspaces.com",
        effective_access_key="key",
        effective_secret_key="secret",
    )
    storage._presign_client()
    assert captured["endpoint_url"] == "https://sfo3.digitaloceanspaces.com"


def test_ensure_https_upgrades_http_download_url():
    assert _ensure_https(
        "http://kampulynk-dev-spaces.sfo3.digitaloceanspaces.com/exports/u/e.zip?sig=1"
    ) == (
        "https://kampulynk-dev-spaces.sfo3.digitaloceanspaces.com/exports/u/e.zip?sig=1"
    )
