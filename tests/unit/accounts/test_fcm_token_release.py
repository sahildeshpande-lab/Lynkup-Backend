from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.accounts.services.device_otp_service import (
    release_fcm_token_from_other_installations,
    upsert_user_installation,
)


def _installation(*, user_id, device_id="device-1", fcm_token="shared-token"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user_id,
        device_id=device_id,
        fcm_token=fcm_token,
        is_active=True,
        last_active_at=datetime.now(timezone.utc),
        platform="android",
    )


@pytest.mark.asyncio
async def test_release_fcm_token_clears_other_users(db_scalars_result):
    keep_user_id = uuid.uuid4()
    other_user_id = uuid.uuid4()
    stale = _installation(user_id=other_user_id)
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=db_scalars_result([stale]))
    mock_db.flush = AsyncMock()
    mock_db.add = MagicMock()

    with patch(
        "apps.notifications.services.topic_service.TopicService.unsubscribe_device_from_user_topics",
        AsyncMock(),
    ) as unsubscribe:
        cleared = await release_fcm_token_from_other_installations(
            mock_db,
            "shared-token",
            keep_user_id=keep_user_id,
        )

    assert cleared == 1
    assert stale.fcm_token is None
    assert stale.is_active is False
    unsubscribe.assert_awaited_once()
    mock_db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_release_fcm_token_noop_for_blank_token():
    mock_db = AsyncMock()
    cleared = await release_fcm_token_from_other_installations(
        mock_db,
        "   ",
        keep_user_id=uuid.uuid4(),
    )
    assert cleared == 0
    mock_db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_user_installation_releases_token_before_assign(db_scalar_result, db_scalars_result):
    user_id = uuid.uuid4()
    other_user_id = uuid.uuid4()
    stale = _installation(user_id=other_user_id)
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(
        side_effect=[
            db_scalars_result([stale]),
            db_scalar_result(None),
        ]
    )
    mock_db.flush = AsyncMock()
    mock_db.add = MagicMock()

    with patch(
        "apps.notifications.services.topic_service.TopicService.unsubscribe_device_from_user_topics",
        AsyncMock(),
    ):
        installation = await upsert_user_installation(
            mock_db,
            user_id,
            "device-new",
            fcm_token="shared-token",
        )

    assert stale.fcm_token is None
    assert stale.is_active is False
    assert installation.user_id == user_id
    assert installation.fcm_token == "shared-token"
    assert installation.is_active is True
