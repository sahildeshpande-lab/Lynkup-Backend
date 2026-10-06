from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
async def test_delete_user_me_soft_deletes_and_runs_side_effects(mock_db, scalar_result):
    user = _user()
    db = mock_db(scalar_result(None))

    with (
        patch.object(svc, "build_user_base_response", AsyncMock(return_value={"id": str(user.id)})),
        patch(
            "apps.profiles.services.profile_stats_service.adjust_counts_for_deleting_user",
            AsyncMock(),
        ) as adjust_counts,
        patch(
            "apps.user_deletion.services.account_recovery_service.run_deletion_request_side_effects",
            AsyncMock(),
        ) as side_effects,
        patch(
            "apps.user_deletion.services.account_recovery_service.deletion_settings"
        ) as ds,
    ):
        ds.account_purge_after_days = 30
        result = await svc.delete_user_me(user, db)

    assert result["deleted"] is True
    assert user.status == UserStatus.deleting
    assert user.is_deleted is True
    assert user.deleted_at is not None
    assert user.purge_after is not None
    assert user.purge_after - user.deleted_at == timedelta(days=30)
    db.commit.assert_awaited_once()
    adjust_counts.assert_not_awaited()
    side_effects.assert_awaited_once()


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
