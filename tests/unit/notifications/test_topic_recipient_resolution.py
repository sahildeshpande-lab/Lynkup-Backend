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


def test_profile_interest_contains_uses_jsonb_contains_operator() -> None:
    """Ensure interest matching uses Postgres @> (not a lost .contains comparator)."""
    clause = repo._profile_interest_contains(218)
    compiled = str(clause.compile(compile_kwargs={"literal_binds": True}))
    assert "@>" in compiled
    assert "[218]" in compiled
    assert '["218"]' in compiled


@pytest.mark.asyncio
async def test_resolve_topic_recipients_to_all_passes_flag() -> None:
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
                (NotificationTargetType.university, [], True),
            ],
        )

    resolve_one.assert_awaited_once()
    assert resolve_one.await_args.kwargs["to_all"] is True
    assert result == [user_a, user_b]


@pytest.mark.asyncio
async def test_resolve_topic_recipients_passes_users_is_alumni() -> None:
    alumni_user = uuid4()
    db = AsyncMock()

    with patch.object(
        repo,
        "_resolve_single_target_recipients",
        AsyncMock(return_value=[alumni_user]),
    ) as resolve_one:
        result = await repo.resolve_topic_recipients(
            db,
            targets=[
                (NotificationTargetType.users, [], True, True),
            ],
        )

    resolve_one.assert_awaited_once()
    assert resolve_one.await_args.kwargs["to_all"] is True
    assert resolve_one.await_args.kwargs["is_alumni"] is True
    assert result == [alumni_user]


@pytest.mark.asyncio
async def test_resolve_users_to_all_applies_alumni_filter() -> None:
    alumni_user = uuid4()
    db = AsyncMock()

    with patch.object(
        repo,
        "_base_visible_profile_user_ids",
        AsyncMock(return_value=[alumni_user]),
    ) as base:
        result = await repo._resolve_single_target_recipients(
            db,
            target_type=NotificationTargetType.users,
            target_values=[],
            to_all=True,
            is_alumni=True,
        )

    assert result == [alumni_user]
    extra_filters = base.await_args.args[1]
    compiled = str(extra_filters[0].compile(compile_kwargs={"literal_binds": True}))
    assert "is_alumni" in compiled.lower()
    assert "true" in compiled.lower()


@pytest.mark.asyncio
async def test_resolve_users_is_alumni_false_does_not_filter() -> None:
    alumni_user = uuid4()
    db = AsyncMock()

    with patch.object(
        repo,
        "_base_visible_profile_user_ids",
        AsyncMock(return_value=[alumni_user]),
    ) as base:
        result = await repo._resolve_single_target_recipients(
            db,
            target_type=NotificationTargetType.users,
            target_values=[],
            to_all=True,
            is_alumni=False,
        )

    assert result == [alumni_user]
    extra_filters = base.await_args.args[1]
    assert extra_filters == []


@pytest.mark.asyncio
async def test_resolve_explicit_users_ignores_is_alumni_false() -> None:
    alumni_user = uuid4()
    db = AsyncMock()

    with patch.object(
        repo,
        "_base_visible_profile_user_ids",
        AsyncMock(return_value=[alumni_user]),
    ) as base:
        result = await repo._resolve_single_target_recipients(
            db,
            target_type=NotificationTargetType.users,
            target_values=[str(alumni_user)],
            to_all=False,
            is_alumni=False,
        )

    assert result == [alumni_user]
    extra_filters = base.await_args.args[1]
    assert len(extra_filters) == 1
    compiled = str(extra_filters[0].compile(compile_kwargs={"literal_binds": True}))
    assert "user_id" in compiled.lower()
    assert "is_alumni" not in compiled.lower()


@pytest.mark.asyncio
async def test_resolve_topic_recipients_combines_to_all_with_specific_filter() -> None:
    all_uni_users = [uuid4(), uuid4()]
    specific_major_users = [all_uni_users[0]]
    db = AsyncMock()

    async def _fake_resolve(_db, *, target_type, target_values, to_all=False):
        if target_type == NotificationTargetType.university:
            assert to_all is True
            return all_uni_users
        if target_type == NotificationTargetType.major:
            assert to_all is False
            assert target_values == ["Computer Science"]
            return specific_major_users
        return []

    with patch.object(
        repo,
        "_resolve_single_target_recipients",
        AsyncMock(side_effect=_fake_resolve),
    ):
        result = await repo.resolve_topic_recipients(
            db,
            targets=[
                (NotificationTargetType.university, [], True),
                (NotificationTargetType.major, ["Computer Science"], False),
            ],
        )

    assert result == [all_uni_users[0]]
