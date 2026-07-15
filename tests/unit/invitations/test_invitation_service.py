from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import Index
from sqlalchemy.sql.schema import CheckConstraint

from apps.invitations.db_models import Invitation
from apps.invitations.services import invitation_service as svc
from common.enums import InvitationStatus


def _invitation(**kwargs):
    now = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)
    defaults = {
        "id": uuid4(),
        "inviter_user_id": uuid4(),
        "code": "ABC1234",
        "status": InvitationStatus.active,
        "redemption_count": 0,
        "redeemed_by_user_id": None,
        "redeemed_at": None,
        "expires_at": now + timedelta(days=7),
        "is_active": True,
        "is_converted": False,
        "deactivated_by": None,
        "deleted_at": None,
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_generate_invitation_code_format():
    code = svc.generate_invitation_code()
    assert len(code) == 7
    assert code[:3].isalpha() and code[:3].isupper()
    assert code[3:].isdigit()


def test_invitation_model_enforces_code_format_constraint_and_indexes():
    check_constraints = [
        arg for arg in Invitation.__table_args__ if isinstance(arg, CheckConstraint)
    ]
    assert any(c.name == "ck_invitations_code_format" for c in check_constraints)
    assert any("^[A-Z]{3}[0-9]{4}$" in str(c.sqltext) for c in check_constraints)

    index_names = {arg.name for arg in Invitation.__table_args__ if isinstance(arg, Index)}
    assert "ix_invitations_code" in index_names
    assert "ix_invitations_inviter_user_id" in index_names
    assert "ix_invitations_status" in index_names
    assert "ix_invitations_expires_at" in index_names
    assert "ix_invitations_deleted_at" in index_names
    assert "ix_invitations_inviter_created_at" in index_names


@pytest.mark.asyncio
async def test_create_invitation_success(mock_db):
    db = mock_db()
    user_id = uuid4()
    created = _invitation(inviter_user_id=user_id, code="ABC1234")

    with (
        patch.object(svc, "count_invitations_created_by_user_between", AsyncMock(return_value=2)),
        patch.object(svc, "invitation_code_exists", AsyncMock(return_value=False)),
        patch.object(svc, "persist_invitation", AsyncMock(return_value=created)) as persist,
        patch.object(svc, "generate_invitation_code", return_value="ABC1234"),
    ):
        response = await svc.create_invitation(db, user_id)

    assert response.status is True
    assert response.message == "Invitation code created successfully"
    assert response.data.code == "ABC1234"
    persist.assert_awaited_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_invitation_daily_limit(mock_db):
    db = mock_db()
    with patch.object(svc, "count_invitations_created_by_user_between", AsyncMock(return_value=50)):
        response = await svc.create_invitation(db, uuid4())

    assert response.status is False
    assert response.message == "Daily invitation limit reached"
    assert response.data is None


@pytest.mark.asyncio
async def test_validate_invitation_success(mock_db):
    db = mock_db()
    invitation = _invitation()
    profile = SimpleNamespace(
        first_name="Sahil",
        last_name="Deshpande",
        profile_photo_url="avatars/sahil.jpg",
        bio="Hello",
    )

    with (
        patch.object(
            svc,
            "get_invitation_with_inviter_details",
            AsyncMock(return_value=(invitation, profile, "Pune University")),
        ),
        patch.object(svc, "generate_profile_image_url", return_value="https://cdn/avatars/sahil.jpg"),
    ):
        response = await svc.validate_invitation(db, invitation.code)

    assert response.status is True
    assert response.message == "Invitation code is valid"
    assert response.data is not None
    assert response.data.user_id == invitation.inviter_user_id
    assert response.data.first_name == "Sahil"
    assert response.data.last_name == "Deshpande"
    assert response.data.profile_photo_url == "https://cdn/avatars/sahil.jpg"
    assert response.data.university == "Pune University"
    assert response.data.bio == "Hello"


@pytest.mark.asyncio
async def test_validate_invitation_expired(mock_db):
    db = mock_db()
    invitation = _invitation(
        expires_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    with patch.object(
        svc,
        "get_invitation_with_inviter_details",
        AsyncMock(return_value=(invitation, None, None)),
    ):
        response = await svc.validate_invitation(db, invitation.code)

    assert response.status is False
    assert response.message == "Invalid or expired invitation code"


@pytest.mark.asyncio
async def test_validate_invitation_soft_deleted(mock_db):
    db = mock_db()
    invitation = _invitation(deleted_at=datetime(2026, 7, 14, tzinfo=timezone.utc))
    with patch.object(
        svc,
        "get_invitation_with_inviter_details",
        AsyncMock(return_value=(invitation, None, None)),
    ):
        response = await svc.validate_invitation(db, invitation.code)

    assert response.status is False
    assert response.message == "Invalid or expired invitation code"


@pytest.mark.asyncio
async def test_validate_invitation_already_redeemed(mock_db):
    db = mock_db()
    invitation = _invitation(redemption_count=1, is_converted=True)
    with patch.object(
        svc,
        "get_invitation_with_inviter_details",
        AsyncMock(return_value=(invitation, None, None)),
    ):
        response = await svc.validate_invitation(db, invitation.code)

    assert response.status is False
    assert response.message == "This code is used, please generate a new code"


@pytest.mark.asyncio
async def test_redeem_invitation_success(mock_db):
    db = mock_db()
    invitation = _invitation()
    redeemer_id = uuid4()

    with patch.object(svc, "get_invitation_by_code_for_update", AsyncMock(return_value=invitation)):
        result = await svc.redeem_invitation(
            db,
            code=invitation.code,
            redeemed_by_user_id=redeemer_id,
            commit=False,
        )

    assert result.redeemed_by_user_id == redeemer_id
    assert result.redemption_count == 1
    assert result.is_converted is True
    assert result.status == InvitationStatus.expired
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_redeem_invitation_rejects_self_redeem(mock_db):
    db = mock_db()
    invitation = _invitation()

    with patch.object(svc, "get_invitation_by_code_for_update", AsyncMock(return_value=invitation)):
        with pytest.raises(HTTPException) as exc:
            await svc.redeem_invitation(
                db,
                code=invitation.code,
                redeemed_by_user_id=invitation.inviter_user_id,
            )

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_get_all_invitations_paginated(mock_db):
    db = mock_db()
    inviter_id = uuid4()
    invitation = _invitation(inviter_user_id=inviter_id, code="XYZ9876")
    user = SimpleNamespace(id=inviter_id)
    profile = SimpleNamespace(first_name="Sahil", last_name="Deshpande")

    with (
        patch.object(svc, "count_invitations", AsyncMock(return_value=1)),
        patch.object(
            svc,
            "list_invitations_with_inviter",
            AsyncMock(return_value=[(invitation, user, profile)]),
        ) as list_mock,
    ):
        response = await svc.get_all_invitations(db, page=1, page_size=20)

    list_mock.assert_awaited_once_with(db, page=1, page_size=20)
    assert response.status is True
    assert response.message == "Invitation codes fetched successfully"
    assert response.data["totalItems"] == 1
    assert response.data["page"] == 1
    assert response.data["pageSize"] == 20
    assert response.data["totalPages"] == 1
    item = response.data["items"][0]
    assert item["code"] == "XYZ9876"
    assert item["user_id"] == inviter_id
    assert item["first_name"] == "Sahil"
    assert item["last_name"] == "Deshpande"
    assert item["username"] == "Sahil Deshpande"
    assert item["status"] == "ACTIVE"
    assert item["deleted_at"] is None
    assert "created_by" not in item


@pytest.mark.asyncio
async def test_get_all_invitations_without_pagination_returns_all(mock_db):
    db = mock_db()
    inviter_id = uuid4()
    invitation = _invitation(inviter_user_id=inviter_id, code="XYZ9876")
    user = SimpleNamespace(id=inviter_id)
    profile = SimpleNamespace(first_name="Sahil", last_name="Deshpande")

    with (
        patch.object(svc, "count_invitations", AsyncMock(return_value=1)),
        patch.object(
            svc,
            "list_invitations_with_inviter",
            AsyncMock(return_value=[(invitation, user, profile)]),
        ) as list_mock,
    ):
        response = await svc.get_all_invitations(db, page=None, page_size=None)

    list_mock.assert_awaited_once_with(db, page=None, page_size=None)
    assert response.data["page"] == 1
    assert response.data["pageSize"] == 1
    assert response.data["totalItems"] == 1
    assert len(response.data["items"]) == 1


@pytest.mark.asyncio
async def test_soft_delete_invitation_success(mock_db):
    db = mock_db()
    invitation = _invitation()
    admin_id = uuid4()

    with patch.object(svc, "get_invitation_by_code_for_update", AsyncMock(return_value=invitation)):
        response = await svc.soft_delete_invitation(
            db,
            code=invitation.code,
            admin_user_id=admin_id,
        )

    assert response.status is True
    assert response.message == "Invitation code deleted successfully"
    assert response.data == {}
    assert invitation.deleted_at is not None
    assert invitation.status == InvitationStatus.deactivated
    assert invitation.is_active is False
    assert invitation.deactivated_by == admin_id
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_soft_delete_invitation_not_found(mock_db):
    db = mock_db()

    with patch.object(svc, "get_invitation_by_code_for_update", AsyncMock(return_value=None)):
        response = await svc.soft_delete_invitation(
            db,
            code="ZZZ0000",
            admin_user_id=uuid4(),
        )

    assert response.status is False
    assert response.message == "Invitation code not found"
    assert response.data == {}
