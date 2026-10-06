from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from apps.notifications.services import notification_service as ns


@pytest.mark.asyncio
async def test_notify_post_recognition_uses_first_name_in_body():
    db = AsyncMock()
    author_id = uuid.uuid4()
    post_id = uuid.uuid4()

    with (
        patch.object(ns, "_ensure_notification_type", AsyncMock(return_value=object())),
        patch.object(ns, "create_notification", AsyncMock(return_value=object())) as create,
    ):
        await ns.notify_post_recognition(
            db,
            author_user_id=author_id,
            post_id=post_id,
            milestone=10,
            first_name="Jane",
        )

    create.assert_awaited_once()
    kwargs = create.await_args.kwargs
    assert kwargs["recipient_user_id"] == author_id
    assert kwargs["notification_type"] == ns.POST_RECOGNITION
    assert kwargs["body"] == "Jane, your post is getting recognized! Keep it up! 🔥"
    assert kwargs["extra"] == {"post_id": str(post_id), "milestone": 10}


@pytest.mark.asyncio
async def test_notify_post_recognition_uses_generic_body_without_first_name():
    db = AsyncMock()

    with (
        patch.object(ns, "_ensure_notification_type", AsyncMock(return_value=object())),
        patch.object(ns, "create_notification", AsyncMock(return_value=object())) as create,
    ):
        await ns.notify_post_recognition(
            db,
            author_user_id=uuid.uuid4(),
            post_id=uuid.uuid4(),
            milestone=10,
            first_name=None,
        )

    assert (
        create.await_args.kwargs["body"]
        == "Your post is getting recognized! Keep it up! 🔥"
    )
