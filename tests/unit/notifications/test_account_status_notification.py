from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from apps.notifications.services import notification_service as ns


@pytest.mark.asyncio
async def test_notify_account_status_builds_title_and_reason_body():
    db = AsyncMock()
    user_id = uuid.uuid4()
    moderator_id = uuid.uuid4()

    with (
        patch.object(ns, "_ensure_notification_type", AsyncMock(return_value=object())),
        patch.object(ns, "create_notification", AsyncMock(return_value=object())) as create,
    ):
        await ns.notify_account_status(
            db,
            user_id=user_id,
            status="suspended",
            reason="Spam activity",
            sender_user_id=moderator_id,
        )

    create.assert_awaited_once()
    kwargs = create.await_args.kwargs
    assert kwargs["recipient_user_id"] == user_id
    assert kwargs["notification_type"] == ns.ACCOUNT_STATUS_CHANGED
    assert kwargs["title"] == "Your account is suspended"
    assert kwargs["body"] == "Spam activity"
    assert kwargs["extra"]["status"] == "suspended"
    assert kwargs["extra"]["reason"] == "Spam activity"


@pytest.mark.asyncio
async def test_notify_account_status_uses_default_body_when_reason_missing():
    db = AsyncMock()

    with (
        patch.object(ns, "_ensure_notification_type", AsyncMock(return_value=object())),
        patch.object(ns, "create_notification", AsyncMock(return_value=object())) as create,
    ):
        await ns.notify_account_status(
            db,
            user_id=uuid.uuid4(),
            status="banned",
            reason=None,
        )

    assert create.await_args.kwargs["title"] == "Your account is banned"
    assert "banned" in create.await_args.kwargs["body"].lower()


def test_admin_user_status_request_requires_note_for_restrictive_statuses():
    from apps.administration.schemas import AdminUserStatusRequest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AdminUserStatusRequest(status="suspended")

    with pytest.raises(ValidationError):
        AdminUserStatusRequest(status="banned", note="   ")

    ok = AdminUserStatusRequest(status="suspended", note=" Abuse ")
    assert ok.note == "Abuse"

    active = AdminUserStatusRequest(status="active")
    assert active.note is None
