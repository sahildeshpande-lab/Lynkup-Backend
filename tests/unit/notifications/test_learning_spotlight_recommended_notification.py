from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.notifications.services import notification_service as ns
from common.enums import SpotlightType


@pytest.mark.asyncio
async def test_notify_learning_spotlight_recommended_uses_first_name_in_body():
    db = AsyncMock()
    user_id = uuid.uuid4()

    with (
        patch.object(ns, "_ensure_notification_type", AsyncMock(return_value=object())),
        patch.object(ns, "create_notification", AsyncMock(return_value=object())) as create,
    ):
        await ns.notify_learning_spotlight_recommended(
            db,
            user_id=user_id,
            spotlight_type=SpotlightType.leading_thinker,
            cycle_day=1,
            first_name="Jane",
        )

    create.assert_awaited_once()
    kwargs = create.await_args.kwargs
    assert kwargs["recipient_user_id"] == user_id
    assert kwargs["notification_type"] == ns.LEARNING_SPOTLIGHT_RECOMMENDED
    assert kwargs["title"] == "Learning Spotlight"
    assert kwargs["body"] == (
        "Jane, your Learning Spotlight articles are ready. Happy Learning 😊"
    )
    assert kwargs["extra"] == {
        "spotlight_type": "leading_thinker",
        "cycle_day": 1,
    }


@pytest.mark.asyncio
async def test_notify_learning_spotlight_recommended_uses_generic_body_without_first_name():
    db = AsyncMock()

    with (
        patch.object(ns, "_ensure_notification_type", AsyncMock(return_value=object())),
        patch.object(ns, "create_notification", AsyncMock(return_value=object())) as create,
    ):
        await ns.notify_learning_spotlight_recommended(
            db,
            user_id=uuid.uuid4(),
            spotlight_type=SpotlightType.influential_research,
            cycle_day=3,
            first_name=None,
        )

    assert (
        create.await_args.kwargs["body"]
        == "Learning Spotlight articles are ready. Happy Learning 😊"
    )


@pytest.mark.asyncio
async def test_notify_learning_spotlight_recommended_uses_profile_first_name() -> None:
    db = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = "Alex"
    db.execute = AsyncMock(return_value=execute_result)

    with (
        patch.object(ns, "_ensure_notification_type", AsyncMock(return_value=object())),
        patch.object(ns, "create_notification", AsyncMock(return_value=object())) as create,
    ):
        await ns.notify_learning_spotlight_recommended(
            db,
            user_id=uuid.uuid4(),
            spotlight_type=SpotlightType.leading_thinker,
            cycle_day=1,
            first_name=None,
        )

    assert (
        create.await_args.kwargs["body"]
        == "Alex, your Learning Spotlight articles are ready. Happy Learning 😊"
    )


@pytest.mark.asyncio
async def test_notify_learning_spotlight_recommended_skips_invalid_cycle_day():
    db = AsyncMock()

    with patch.object(ns, "create_notification", AsyncMock()) as create:
        result = await ns.notify_learning_spotlight_recommended(
            db,
            user_id=uuid.uuid4(),
            spotlight_type=SpotlightType.leading_thinker,
            cycle_day=0,
        )

    assert result is None
    create.assert_not_awaited()
