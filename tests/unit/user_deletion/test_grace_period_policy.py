from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from apps.accounts.db_models import User
from apps.profiles.services import profile_service as profile_svc
from apps.user_deletion.services import account_recovery_service as recovery_svc
from common.enums import UserStatus
from common.user_visibility import (
    HIDDEN_ACCOUNT_STATUSES,
    is_hidden_account_status,
    visible_user_filters,
)


def _user(**overrides):
    now = datetime.now(timezone.utc)
    data = {
        "id": uuid4(),
        "status": UserStatus.active,
        "is_deleted": False,
        "deleted_at": None,
        "purge_after": None,
        "firebase_uid": "firebase-uid",
        "email": "user@example.com",
        "updated_at": now,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


@pytest.mark.asyncio
async def test_delete_user_me_sets_state_without_content_mutation(mock_db, scalar_result):
    user = _user()
    db = mock_db(scalar_result(None))

    with (
        patch.object(
            profile_svc,
            "build_user_base_response",
            AsyncMock(return_value={"id": str(user.id)}),
        ),
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
        patch(
            "apps.user_deletion.services.account_deletion_service.AccountDeletionService.purge_user",
            AsyncMock(),
        ) as purge_user,
    ):
        ds.account_purge_after_days = 30
        result = await profile_svc.delete_user_me(user, db)

    assert result["deleted"] is True
    assert user.status == UserStatus.deleting
    assert user.is_deleted is True
    assert user.deleted_at is not None
    assert user.purge_after is not None
    assert user.purge_after - user.deleted_at == timedelta(days=30)
    adjust_counts.assert_not_awaited()
    side_effects.assert_awaited_once()
    purge_user.assert_not_awaited()


@pytest.mark.asyncio
async def test_deletion_side_effects_revoke_firebase_not_disable():
    user = _user(status=UserStatus.deleting, is_deleted=True)
    db = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    with (
        patch(
            "apps.accounts.services.device_otp_service.deactivate_push_for_user_installations",
            AsyncMock(),
        ),
        patch(
            "apps.chat.service.deactivate_stream_user_best_effort",
            AsyncMock(),
        ) as deactivate_stream,
        patch(
            "core.auth.services.revoke_firebase_tokens"
        ) as revoke,
        patch(
            "core.auth.services.disable_firebase_user"
        ) as disable,
        patch(
            "core.auth.services.delete_firebase_user"
        ) as delete_fb,
    ):
        await recovery_svc.run_deletion_request_side_effects(user, db)

    revoke.assert_called_once_with(user.firebase_uid)
    disable.assert_not_called()
    delete_fb.assert_not_called()
    deactivate_stream.assert_awaited_once()


@pytest.mark.asyncio
async def test_recovery_restores_flags_and_reactivates_stream_only():
    now = datetime.now(timezone.utc)
    user = _user(
        status=UserStatus.deleting,
        is_deleted=True,
        deleted_at=now,
        purge_after=now + timedelta(days=10),
    )
    db = AsyncMock()
    db.add = lambda *a, **k: None

    with patch(
        "apps.chat.service.reactivate_stream_user_best_effort",
        AsyncMock(),
    ) as reactivate:
        restored = await recovery_svc.restore_deleting_account_if_eligible(
            user, db, now=now
        )
        await recovery_svc.run_recovery_side_effects(user, db)

    assert restored is True
    assert user.status == UserStatus.active
    assert user.is_deleted is False
    assert user.deleted_at is None
    assert user.purge_after is None
    reactivate.assert_awaited_once()


def test_deleting_status_is_not_hidden_for_content():
    assert UserStatus.deleting not in HIDDEN_ACCOUNT_STATUSES
    assert is_hidden_account_status(UserStatus.deleting) is False
    assert is_hidden_account_status(UserStatus.suspended) is True


def test_visible_user_filters_allow_grace_period_deleting_users():
    filters = visible_user_filters(User)
    assert len(filters) == 2
    # Grace-period deleting users are OR'd in even when is_deleted/deleted_at are set.
    compiled = str(
        filters[1].compile(compile_kwargs={"literal_binds": True})
    ).lower()
    assert "deleting" in compiled
    assert "is_deleted" in compiled


@pytest.mark.asyncio
async def test_purge_user_not_invoked_from_deletion_request(mock_db, scalar_result):
    """Permanent purge must only run from the expired-account worker."""
    user = _user()
    db = mock_db(scalar_result(None))

    with (
        patch.object(
            profile_svc,
            "build_user_base_response",
            AsyncMock(return_value={"id": str(user.id)}),
        ),
        patch(
            "apps.user_deletion.services.account_recovery_service.run_deletion_request_side_effects",
            AsyncMock(),
        ),
        patch(
            "apps.user_deletion.services.account_recovery_service.deletion_settings"
        ) as ds,
        patch(
            "apps.user_deletion.services.account_deletion_service.text",
            side_effect=AssertionError("purge_user_data must not run on deletion request"),
        ),
    ):
        ds.account_purge_after_days = 30
        await profile_svc.delete_user_me(user, db)

    assert user.status == UserStatus.deleting
