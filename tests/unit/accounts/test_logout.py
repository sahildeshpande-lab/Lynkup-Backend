from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from apps.accounts.schemas import LogoutRequest


@pytest.mark.asyncio
async def test_logout_deactivates_current_installation(db_scalar_result, db_scalars_result):
    from apps.accounts.services import logout

    user = MagicMock()
    user.id = uuid4()
    user.firebase_uid = None
    installation = MagicMock()
    installation.is_active = True
    installation.last_active_at = None

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(
        side_effect=[
            db_scalar_result(installation),
            db_scalars_result([]),
        ]
    )
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    result = await logout(LogoutRequest(device_id="device-123"), mock_db, user)

    assert result is None
    assert installation.is_active is False
    assert installation.last_active_at is not None
    mock_db.add.assert_called_once_with(installation)
    mock_db.commit.assert_awaited_once()
