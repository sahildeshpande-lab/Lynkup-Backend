from __future__ import annotations

import pytest
from unittest.mock import MagicMock
import httpx
from botocore.exceptions import ClientError
from core.images.config import (
    build_image_key,
    normalize_image_name,
    generate_upload_url,
    generate_download_url,
    delete_file,
    file_exists,
    upload_image_to_s3,
    settings,
)

def test_build_image_key():
    assert build_image_key("test.jpg") == "images/test.jpg"
    assert build_image_key("test.jpg", "profiles") == "profiles/test.jpg"

def test_normalize_image_name():
    assert normalize_image_name("") == ""
    assert normalize_image_name(None) == ""
    assert normalize_image_name("  test.jpg  ") == "test.jpg"

def test_generate_upload_url_no_bucket(monkeypatch):
    monkeypatch.setattr(settings, "aws_s3_bucket", None)
    assert generate_upload_url("test.jpg") == ""

def test_generate_upload_url_success(monkeypatch):
    monkeypatch.setattr(settings, "aws_s3_bucket", "test-bucket")
    mock_s3 = MagicMock()
    mock_s3.generate_presigned_url.return_value = "https://presigned-upload-url"
    monkeypatch.setattr("core.images.config.s3_client", mock_s3)

    url = generate_upload_url("test.jpg")
    assert url == "https://presigned-upload-url"
    mock_s3.generate_presigned_url.assert_called_once_with(
        "put_object",
        Params={"Bucket": "test-bucket", "Key": "test.jpg"},
        ExpiresIn=3600
    )

def test_generate_upload_url_client_error(monkeypatch):
    monkeypatch.setattr(settings, "aws_s3_bucket", "test-bucket")
    mock_s3 = MagicMock()
    mock_s3.generate_presigned_url.side_effect = ClientError(
        {"Error": {"Code": "SomeError", "Message": "Msg"}}, "generate_presigned_url"
    )
    monkeypatch.setattr("core.images.config.s3_client", mock_s3)

    assert generate_upload_url("test.jpg") == ""

def test_generate_download_url(monkeypatch):
    # Empty bucket or file name
    monkeypatch.setattr(settings, "aws_s3_bucket", None)
    assert generate_download_url("test.jpg") == "/static/uploads/test.jpg"
    
    monkeypatch.setattr(settings, "aws_s3_bucket", "test-bucket")
    assert generate_download_url("") == ""
    
    # Already a url
    assert generate_download_url("http://external.com/img.jpg") == "http://external.com/img.jpg"
    assert generate_download_url("https://external.com/img.jpg") == "https://external.com/img.jpg"

    # Success path
    mock_s3 = MagicMock()
    mock_s3.generate_presigned_url.return_value = "https://presigned-download-url"
    monkeypatch.setattr("core.images.config.s3_client", mock_s3)
    assert generate_download_url("test.jpg") == "https://presigned-download-url"

    # ClientError path
    mock_s3.generate_presigned_url.side_effect = ClientError(
        {"Error": {"Code": "SomeError", "Message": "Msg"}}, "generate_presigned_url"
    )
    assert generate_download_url("test.jpg") == ""

def test_delete_file(monkeypatch):
    # No bucket/file name
    monkeypatch.setattr(settings, "aws_s3_bucket", None)
    delete_file("test.jpg") # should return without crash
    
    monkeypatch.setattr(settings, "aws_s3_bucket", "test-bucket")
    delete_file("") # should return without crash

    # Success path
    mock_s3 = MagicMock()
    monkeypatch.setattr("core.images.config.s3_client", mock_s3)
    delete_file("test.jpg")
    mock_s3.delete_object.assert_called_once_with(Bucket="test-bucket", Key="test.jpg")

    # ClientError path
    mock_s3.delete_object.side_effect = ClientError(
        {"Error": {"Code": "SomeError", "Message": "Msg"}}, "delete_object"
    )
    delete_file("test.jpg") # should catch error and return without crash

def test_file_exists(monkeypatch):
    monkeypatch.setattr(settings, "aws_s3_bucket", None)
    assert file_exists("test.jpg") is False
    
    monkeypatch.setattr(settings, "aws_s3_bucket", "test-bucket")
    assert file_exists("") is False

    # Success path (exists)
    mock_s3 = MagicMock()
    monkeypatch.setattr("core.images.config.s3_client", mock_s3)
    assert file_exists("test.jpg") is True
    mock_s3.head_object.assert_called_once_with(Bucket="test-bucket", Key="test.jpg")

    # Error path (404)
    mock_s3.head_object.side_effect = ClientError(
        {"Error": {"Code": "404", "Message": "Not Found"}}, "head_object"
    )
    assert file_exists("test.jpg") is False

    # Error path (other error)
    mock_s3.head_object.side_effect = ClientError(
        {"Error": {"Code": "500", "Message": "Internal Error"}}, "head_object"
    )
    assert file_exists("test.jpg") is False

@pytest.mark.asyncio
async def test_upload_image_to_s3(monkeypatch):
    # Empty data
    assert await upload_image_to_s3("") == ""
    assert await upload_image_to_s3(None) == ""

    monkeypatch.setattr(settings, "aws_s3_bucket", "test-bucket")

    # Already S3 URL of our bucket
    assert await upload_image_to_s3("http://test-bucket.s3.amazonaws.com/image.jpg") == "http://test-bucket.s3.amazonaws.com/image.jpg"

    # Base64 data URI success
    mock_s3 = MagicMock()
    mock_s3.generate_presigned_url.return_value = "https://test-bucket/profiles/mocked.png"
    monkeypatch.setattr("core.images.config.s3_client", mock_s3)
    
    base64_data = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    res = await upload_image_to_s3(base64_data)
    assert res == "https://test-bucket/profiles/mocked.png"
    assert mock_s3.put_object.call_count == 1

    # Base64 data URI fail (invalid base64 format or exception)
    res_fail = await upload_image_to_s3("data:image/png;base64")
    # should catch exception and return original string
    assert res_fail == "data:image/png;base64"

    # Normal string (not url, not base64)
    assert await upload_image_to_s3("simple_image_name.jpg") == "simple_image_name.jpg"

    # HTTP URL download success
    mock_s3.reset_mock()
    mock_s3.generate_presigned_url.return_value = "https://test-bucket/profiles/downloaded.jpg"
    
    # Mock httpx.AsyncClient response
    class MockResponse:
        def __init__(self, status_code, content, headers):
            self.status_code = status_code
            self.content = content
            self.headers = headers
    
    class MockClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
        async def get(self, url, timeout=10.0):
            if "success" in url:
                return MockResponse(200, b"fake-image-bytes", {"content-type": "image/jpeg"})
            return MockResponse(404, b"", {})

    monkeypatch.setattr(httpx, "AsyncClient", MockClient)
    
    res_http = await upload_image_to_s3("http://example.com/success.jpg")
    assert res_http == "https://test-bucket/profiles/downloaded.jpg"
    assert mock_s3.put_object.call_count == 1

    # HTTP URL download failure (404 status)
    mock_s3.reset_mock()
    res_http_fail = await upload_image_to_s3("http://example.com/fail.jpg")
    assert res_http_fail == "http://example.com/fail.jpg"
    assert mock_s3.put_object.call_count == 0

    # HTTP URL download exception
    class BadClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
        async def get(self, url, timeout=10.0):
            raise Exception("Connection error")
    
    monkeypatch.setattr(httpx, "AsyncClient", BadClient)
    res_http_exc = await upload_image_to_s3("http://example.com/error.jpg")
    assert res_http_exc == "http://example.com/error.jpg"
