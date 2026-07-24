from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from apps.accounts.schemas import LogoutRequest


@pytest.mark.asyncio
async def test_logout_deactivates_current_installation(db_scalar_result, db_scalars_result, monkeypatch):
    from apps.accounts.services import logout

    user = MagicMock()
    user.id = uuid4()
    user.firebase_uid = None
    user.email_verified_at = "verified"
    user.email_otp = "1234"
    user.email_otp_created_at = "now"
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
    mock_db.delete.assert_not_called()
    assert installation.is_active is False
    # Manual logout keeps email verification so the same device skips OTP next time.
    assert user.email_verified_at == "verified"
    assert user.email_otp is None
    assert user.email_otp_created_at is None
    mock_db.add.assert_called()
    mock_db.commit.assert_awaited_once()
