from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from apps.engagement.services import post_recognition_service as svc
from common.post_recognition import select_post_recognition_milestone_for_notification


def test_select_post_recognition_milestone_for_notification_prefers_highest():
    assert select_post_recognition_milestone_for_notification([10, 20, 50]) == 50
    assert select_post_recognition_milestone_for_notification([10]) == 10
    assert select_post_recognition_milestone_for_notification([]) is None


@pytest.mark.asyncio
async def test_notify_post_recognition_skips_when_no_milestone_crossed():
    db = AsyncMock()
    with patch.object(svc, "_try_acquire_post_recognition_lock", AsyncMock()) as lock:
        await svc.notify_post_recognition_milestones_best_effort(
            db,
            author_user_id=uuid.uuid4(),
            post_id=uuid.uuid4(),
            crossed_milestones=[],
        )
    lock.assert_not_called()


@pytest.mark.asyncio
async def test_notify_post_recognition_sends_one_notification_for_crossed_milestones():
    db = AsyncMock()
    author_id = uuid.uuid4()
    post_id = uuid.uuid4()

    with (
        patch.object(svc, "_try_acquire_post_recognition_lock", AsyncMock(return_value=True)),
        patch.object(svc, "_release_post_recognition_lock", AsyncMock()) as release,
        patch(
            "apps.notifications.repositories.notification_repository.has_recent_post_recognition_notification",
            AsyncMock(return_value=False),
        ),
        patch.object(svc, "_author_first_name", AsyncMock(return_value="Jane")),
        patch(
            "apps.notifications.services.notification_service.notify_post_recognition",
            AsyncMock(),
        ) as notify,
    ):
        await svc.notify_post_recognition_milestones_best_effort(
            db,
            author_user_id=author_id,
            post_id=post_id,
            crossed_milestones=[10, 20, 50],
        )

    notify.assert_awaited_once_with(
        db,
        author_user_id=author_id,
        post_id=post_id,
        milestone=50,
        first_name="Jane",
    )
    release.assert_awaited_once()


@pytest.mark.asyncio
async def test_notify_post_recognition_skips_recent_duplicate_for_same_milestone():
    db = AsyncMock()
    author_id = uuid.uuid4()
    post_id = uuid.uuid4()

    with (
        patch.object(svc, "_try_acquire_post_recognition_lock", AsyncMock(return_value=True)),
        patch.object(svc, "_release_post_recognition_lock", AsyncMock()) as release,
        patch(
            "apps.notifications.repositories.notification_repository.has_recent_post_recognition_notification",
            AsyncMock(return_value=True),
        ),
        patch(
            "apps.notifications.services.notification_service.notify_post_recognition",
            AsyncMock(),
        ) as notify,
    ):
        await svc.notify_post_recognition_milestones_best_effort(
            db,
            author_user_id=author_id,
            post_id=post_id,
            crossed_milestones=[10],
        )

    notify.assert_not_called()
    release.assert_awaited_once()


@pytest.mark.asyncio
async def test_notify_post_recognition_skips_when_lock_not_acquired():
    db = AsyncMock()

    with (
        patch.object(svc, "_try_acquire_post_recognition_lock", AsyncMock(return_value=False)),
        patch.object(svc, "_release_post_recognition_lock", AsyncMock()) as release,
        patch(
            "apps.notifications.services.notification_service.notify_post_recognition",
            AsyncMock(),
        ) as notify,
    ):
        await svc.notify_post_recognition_milestones_best_effort(
            db,
            author_user_id=uuid.uuid4(),
            post_id=uuid.uuid4(),
            crossed_milestones=[10],
        )

    notify.assert_not_called()
    release.assert_not_called()
