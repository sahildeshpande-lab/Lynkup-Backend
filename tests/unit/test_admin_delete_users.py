from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from apps.administration.services import user_management_service as svc
from common.enums import UserStatus


def _user(user_id=None, *, role: str = "user", firebase_uid: str | None = "firebase-uid"):
    user = SimpleNamespace(
        id=user_id or uuid4(),
        firebase_uid=firebase_uid,
        status=UserStatus.active,
        deleted_at=None,
        is_deleted=False,
        deleted_users=None,
        purge_after=None,
        role=role,
        roles=[],
    )
    return user


@pytest.mark.asyncio
async def test_admin_delete_users_soft_deletes_user_role(monkeypatch, mock_db, scalar_result):
    user = _user(role="user")
    db = mock_db(scalar_result(user), scalar_result(None))

    monkeypatch.setattr(svc, "build_user_base_response", AsyncMock(return_value={"id": str(user.id)}))

    result = await svc.admin_delete_users([str(user.id)], "user", db)

    assert len(result["deleted_users"]) == 1
    assert user.status == UserStatus.deleting
    assert user.is_deleted is True
    assert user.deleted_at is not None
    assert user.purge_after is not None
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_delete_users_skips_users_with_different_role(mock_db, scalar_result):
    viewer = _user(role="viewer")
    db = mock_db(scalar_result(viewer))

    result = await svc.admin_delete_users([str(viewer.id)], "user", db)

    assert result["deleted_users"] == []
    assert viewer.is_deleted is False
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_delete_users_reassigns_posts_before_moderator_delete(monkeypatch, mock_db, scalar_result):
    moderator = _user(role="moderator")
    superadmin = _user(role="superadmin", firebase_uid="admin-super")
    db = mock_db(scalar_result(superadmin), scalar_result(moderator), scalar_result(None))

    reassign = AsyncMock()
    monkeypatch.setattr(svc, "_reassign_moderator_holdings_to_superadmin", reassign)
    monkeypatch.setattr(svc, "build_user_base_response", AsyncMock(return_value={"id": str(moderator.id)}))

    result = await svc.admin_delete_users([str(moderator.id)], "moderator", db)

    reassign.assert_awaited_once()
    assert reassign.await_args.args[1] == moderator.id
    assert reassign.await_args.args[2] == superadmin.id
    assert len(result["deleted_users"]) == 1
    assert moderator.is_deleted is True


@pytest.mark.asyncio
async def test_admin_delete_users_requires_superadmin_for_moderator_role(mock_db, scalar_result):
    db = mock_db(scalar_result(None))

    with pytest.raises(HTTPException) as exc:
        await svc.admin_delete_users([str(uuid4())], "moderator", db)

    assert exc.value.status_code == 404
    assert exc.value.detail == "Super Admin not found"


@pytest.mark.asyncio
async def test_admin_delete_users_bulk_moderator_reassignment(monkeypatch, mock_db, scalar_result):
    superadmin = _user(role="superadmin", firebase_uid="admin-super")
    moderator_one = _user(role="moderator")
    moderator_two = _user(role="moderator")
    db = mock_db(
        scalar_result(superadmin),
        scalar_result(moderator_one),
        scalar_result(None),
        scalar_result(moderator_two),
        scalar_result(None),
    )

    reassign = AsyncMock()
    monkeypatch.setattr(svc, "_reassign_moderator_holdings_to_superadmin", reassign)
    monkeypatch.setattr(
        svc,
        "build_user_base_response",
        AsyncMock(side_effect=[{"id": str(moderator_one.id)}, {"id": str(moderator_two.id)}]),
    )

    result = await svc.admin_delete_users(
        [str(moderator_one.id), str(moderator_two.id)],
        "moderator",
        db,
    )

    assert reassign.await_count == 2
    assert len(result["deleted_users"]) == 2
