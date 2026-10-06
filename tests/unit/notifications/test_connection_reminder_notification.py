from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from apps.notifications.services import notification_service as ns


@pytest.mark.asyncio
async def test_notify_connection_reminder_single_sender_with_name():
    db = AsyncMock()
    recipient_id = uuid.uuid4()
    sender_id = uuid.uuid4()

    with (
        patch.object(ns, "_ensure_notification_type", AsyncMock(return_value=object())),
        patch.object(ns, "create_notification", AsyncMock(return_value=object())) as create,
    ):
        await ns.notify_connection_reminder(
            db,
            recipient_user_id=recipient_id,
            pending_count=1,
            sender_name="Blake",
            sender_user_id=sender_id,
        )

    create.assert_awaited_once()
    kwargs = create.await_args.kwargs
    assert kwargs["recipient_user_id"] == recipient_id
    assert kwargs["notification_type"] == ns.CONNECTION_REMINDER
    assert kwargs["title"] == "LynkUp Reminder"
    assert kwargs["body"] == "You have a pending LynkUp request from Blake."
    assert kwargs["sender_user_id"] == sender_id
    assert kwargs["extra"] == {"pending_count": 1}
    assert kwargs["send_push"] is True


@pytest.mark.asyncio
async def test_notify_connection_reminder_single_sender_without_name():
    db = AsyncMock()
    recipient_id = uuid.uuid4()

    with (
        patch.object(ns, "_ensure_notification_type", AsyncMock(return_value=object())),
        patch.object(ns, "create_notification", AsyncMock(return_value=object())) as create,
    ):
        await ns.notify_connection_reminder(
            db,
            recipient_user_id=recipient_id,
            pending_count=1,
            sender_name=None,
        )

    assert (
        create.await_args.kwargs["body"]
        == "You have a pending LynkUp request waiting for your response."
    )


@pytest.mark.asyncio
async def test_notify_connection_reminder_multiple_senders():
    db = AsyncMock()
    recipient_id = uuid.uuid4()

    with (
        patch.object(ns, "_ensure_notification_type", AsyncMock(return_value=object())),
        patch.object(ns, "create_notification", AsyncMock(return_value=object())) as create,
    ):
        await ns.notify_connection_reminder(
            db,
            recipient_user_id=recipient_id,
            pending_count=3,
        )

    create.assert_awaited_once()
    kwargs = create.await_args.kwargs
    assert kwargs["recipient_user_id"] == recipient_id
    assert kwargs["notification_type"] == ns.CONNECTION_REMINDER
    assert kwargs["title"] == "LynkUp Reminder"
    assert kwargs["body"] == "You have 3 pending LynkUp requests waiting for your response."
    assert kwargs["extra"] == {"pending_count": 3}


@pytest.mark.asyncio
async def test_notify_connection_reminder_skips_zero_pending():
    db = AsyncMock()

    with patch.object(ns, "create_notification", AsyncMock()) as create:
        result = await ns.notify_connection_reminder(
            db,
            recipient_user_id=uuid.uuid4(),
            pending_count=0,
        )

    assert result is None
    create.assert_not_awaited()
