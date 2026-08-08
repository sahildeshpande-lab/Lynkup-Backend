from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from apps.notifications.repositories import campaign_audience_repository as repo
from common.enums import NotificationTargetType


@pytest.mark.asyncio
async def test_resolve_topic_recipients_ors_within_same_type() -> None:
    user_a = uuid4()
    user_b = uuid4()
    db = AsyncMock()

    with patch.object(
        repo,
        "_resolve_single_target_recipients",
        AsyncMock(return_value=[user_a, user_b]),
    ) as resolve_one:
        result = await repo.resolve_topic_recipients(
            db,
            targets=[
                (NotificationTargetType.university, ["uni-a", "uni-b"]),
            ],
        )

    resolve_one.assert_awaited_once()
    assert resolve_one.await_args.kwargs["target_values"] == ["uni-a", "uni-b"]
    assert result == [user_a, user_b]


@pytest.mark.asyncio
async def test_resolve_topic_recipients_ands_across_different_types() -> None:
    uni_only = uuid4()
    both = uuid4()
    major_only = uuid4()
    db = AsyncMock()

    async def _fake_resolve(_db, *, target_type, target_values):
        _ = target_values
        if target_type == NotificationTargetType.university:
            return [uni_only, both]
        if target_type == NotificationTargetType.major:
            return [both, major_only]
        return []

    with patch.object(
        repo,
        "_resolve_single_target_recipients",
        AsyncMock(side_effect=_fake_resolve),
    ):
        result = await repo.resolve_topic_recipients(
            db,
            targets=[
                (NotificationTargetType.university, ["uni-a"]),
                (NotificationTargetType.major, ["major-b"]),
            ],
        )

    assert result == [both]


@pytest.mark.asyncio
async def test_resolve_topic_recipients_and_with_or_inside_major() -> None:
    match_major_a = uuid4()
    match_major_b = uuid4()
    other_uni = uuid4()
    db = AsyncMock()

    async def _fake_resolve(_db, *, target_type, target_values):
        if target_type == NotificationTargetType.university:
            return [match_major_a, match_major_b]
        if target_type == NotificationTargetType.major:
            assert set(target_values) == {"major-a", "major-b"}
            return [match_major_a, match_major_b, other_uni]
        return []

    with patch.object(
        repo,
        "_resolve_single_target_recipients",
        AsyncMock(side_effect=_fake_resolve),
    ):
        result = await repo.resolve_topic_recipients(
            db,
            targets=[
                (NotificationTargetType.university, ["uni-a"]),
                (NotificationTargetType.major, ["major-a", "major-b"]),
            ],
        )

    assert result == [match_major_a, match_major_b]


@pytest.mark.asyncio
async def test_resolve_topic_recipients_merges_duplicate_types_with_or() -> None:
    user_a = uuid4()
    user_b = uuid4()
    db = AsyncMock()

    with patch.object(
        repo,
        "_resolve_single_target_recipients",
        AsyncMock(return_value=[user_a, user_b]),
    ) as resolve_one:
        result = await repo.resolve_topic_recipients(
            db,
            targets=[
                (NotificationTargetType.university, ["uni-a"]),
                (NotificationTargetType.university, ["uni-b"]),
            ],
        )

    resolve_one.assert_awaited_once()
    assert resolve_one.await_args.kwargs["target_values"] == ["uni-a", "uni-b"]
    assert result == [user_a, user_b]
