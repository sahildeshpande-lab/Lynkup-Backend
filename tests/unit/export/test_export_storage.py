from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from apps.export.storage import DigitalOceanSpacesExportStorage, _normalize_key


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


def test_spaces_generate_download_url_is_presigned():
    client = MagicMock()
    client.generate_presigned_url.return_value = (
        "https://kampulynk-dev-spaces.sfo3.digitaloceanspaces.com/exports/u/e.zip"
        "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=abc"
    )
    storage = DigitalOceanSpacesExportStorage()
    storage._client = client
    storage._settings = MagicMock(effective_bucket="kampulynk-dev-spaces")

    url = storage.generate_download_url("exports/u/e.zip", expires_in=600)
    assert "X-Amz-Signature" in url
    assert "cdn.digitaloceanspaces.com" not in url
    client.generate_presigned_url.assert_called_once()
    call_kwargs = client.generate_presigned_url.call_args
    assert call_kwargs.args[0] == "get_object"
    assert call_kwargs.kwargs["ExpiresIn"] == 600


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
