from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from apps.profiles.services import profile_service as svc
from common.enums import UserStatus


def _user(*, status=UserStatus.active, is_deleted=False, deleted_at=None, firebase_uid="firebase-uid"):
    return SimpleNamespace(
        id=uuid4(),
        status=status,
        is_deleted=is_deleted,
        deleted_at=deleted_at,
        purge_after=None,
        firebase_uid=firebase_uid,
        email="user@example.com",
    )


@pytest.mark.asyncio
async def test_delete_user_me_soft_deletes_and_disables_firebase(mock_db, scalar_result):
    user = _user()
    db = mock_db(scalar_result(None))

    with (
        patch.object(svc, "build_user_base_response", AsyncMock(return_value={"id": str(user.id)})),
        patch(
            "apps.profiles.services.profile_service.disable_firebase_user",
            create=True,
        ),
        patch("core.auth.services.disable_firebase_user") as disable_firebase,
    ):
        # Patch where the service imports from
        with patch(
            "core.auth.services.disable_firebase_user",
            disable_firebase,
        ):
            # Re-import path used inside the function
            result = await svc.delete_user_me(user, db)

    assert result["deleted"] is True
    assert user.status == UserStatus.deleting
    assert user.is_deleted is True
    assert user.deleted_at is not None
    assert user.purge_after is not None
    db.commit.assert_awaited_once()
    disable_firebase.assert_called_once_with("firebase-uid")


@pytest.mark.asyncio
async def test_delete_user_me_idempotent_when_already_deleting(mock_db, scalar_result):
    now = datetime.now(timezone.utc)
    user = _user(status=UserStatus.deleting, is_deleted=True, deleted_at=now)
    user.purge_after = now
    db = mock_db(scalar_result(None))

    with patch.object(svc, "build_user_base_response", AsyncMock(return_value={"id": str(user.id)})):
        result = await svc.delete_user_me(user, db)

    assert result["deleted"] is True
    db.commit.assert_not_called()
