from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.push.dispatcher import (
    PLATFORM_ANDROID,
    PLATFORM_IOS,
    PushTarget,
    normalize_platform,
    send_push_to_device,
    send_push_to_devices,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, PLATFORM_ANDROID),
        ("", PLATFORM_ANDROID),
        ("android", PLATFORM_ANDROID),
        ("ANDROID", PLATFORM_ANDROID),
        ("ios", PLATFORM_IOS),
        ("iPhone", PLATFORM_IOS),
        ("ipad", PLATFORM_IOS),
        ("unknown", PLATFORM_ANDROID),
    ],
)
def test_normalize_platform(raw, expected) -> None:
    assert normalize_platform(raw) == expected


@pytest.mark.asyncio
async def test_send_push_to_device_requires_token() -> None:
    with pytest.raises(ValueError, match="token cannot be blank"):
        await send_push_to_device(token="  ", platform="android", title="t", body="b")


@pytest.mark.asyncio
async def test_send_push_to_device_routes_ios() -> None:
    with patch(
        "core.push.dispatcher.send_apns_notification",
        new=AsyncMock(return_value="apns-1"),
    ) as apns:
        result = await send_push_to_device(
            token="ios-token",
            platform="ios",
            title="Hello",
            body="World",
            data={"k": "v"},
        )
    assert result == "apns-1"
    apns.assert_awaited_once_with("ios-token", "Hello", "World", {"k": "v"})


@pytest.mark.asyncio
async def test_send_push_to_device_routes_android() -> None:
    with patch(
        "core.push.dispatcher.send_push_notification",
        return_value="fcm-1",
    ) as fcm:
        result = await send_push_to_device(
            token="android-token",
            platform="android",
            title="Hello",
            body="World",
        )
    assert result == "fcm-1"
    fcm.assert_called_once_with("android-token", "Hello", "World", None)


@pytest.mark.asyncio
async def test_send_push_to_devices_skips_empty_targets() -> None:
    result = await send_push_to_devices([], "t", "b")
    assert result == {
        "successful_count": 0,
        "failed_count": 0,
        "failed_tokens": [],
    }


@pytest.mark.asyncio
async def test_send_push_to_devices_splits_platforms_and_dedupes() -> None:
    android = MagicMock(
        return_value={
            "successful_count": 1,
            "failed_count": 1,
            "failed_tokens": ["bad-android"],
        }
    )
    ios = AsyncMock(
        return_value={
            "successful_count": 2,
            "failed_count": 0,
            "failed_tokens": [],
        }
    )
    with (
        patch("core.push.dispatcher.send_push_notifications", android),
        patch("core.push.dispatcher.send_apns_notifications", ios),
    ):
        result = await send_push_to_devices(
            [
                PushTarget(token="a1", platform="android"),
                PushTarget(token="a1", platform="android"),
                ("  ", "android"),
                ("i1", "ios"),
                PushTarget(token="i2", platform="iphone"),
            ],
            "Title",
            "Body",
            {"x": "1"},
        )

    assert result["successful_count"] == 3
    assert result["failed_count"] == 1
    assert result["failed_tokens"] == ["bad-android"]
    android.assert_called_once_with(["a1"], "Title", "Body", {"x": "1"})
    ios.assert_awaited_once_with(["i1", "i2"], "Title", "Body", {"x": "1"})
