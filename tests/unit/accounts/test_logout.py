from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from apps.accounts.schemas import LogoutRequest


@pytest.mark.asyncio
async def test_logout_deletes_current_installation(db_scalar_result, db_scalars_result, monkeypatch):
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
            db_scalar_result(user),
            db_scalar_result(installation),
            db_scalars_result([]),
        ]
    )
    mock_db.add = MagicMock()
    mock_db.delete = AsyncMock()
    mock_db.commit = AsyncMock()

    monkeypatch.setattr("apps.accounts.services.revoke_firebase_tokens", lambda *args, **kwargs: None)

    result = await logout(
        LogoutRequest(firebaseId="firebase-token", device_id="device-123"),
        {"uid": str(user.id)},
        mock_db,
    )

    assert result is None
    mock_db.delete.assert_awaited_once_with(installation)
    mock_db.add.assert_not_called()
    mock_db.commit.assert_awaited_once()
