from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.engagement.repositories.post_reaction_list_repository import (
    LatestReactorProfileRow,
    LatestReactorReactionRow,
    _map_latest_reactor_rows,
    fetch_latest_reactors_for_posts,
)
from apps.engagement.services.post_reaction_formatters import build_post_reactions_from_rows
from common.enums import ReactionType


def _sql_row(
    post_id: uuid.UUID,
    *,
    reaction_type: str = "like",
    created_at: datetime | None = None,
    profile_user_id: uuid.UUID | None = None,
    first_name: str | None = "Ada",
    last_name: str | None = "Lovelace",
):
    user_id = uuid.uuid4()
    return {
        "post_id": post_id,
        "user_id": user_id,
        "reaction_type": reaction_type,
        "created_at": created_at or datetime(2026, 1, 1, tzinfo=timezone.utc),
        "profile_user_id": profile_user_id,
        "first_name": first_name,
        "last_name": last_name,
        "profile_photo_url": "photo.jpg",
        "bio": "bio",
    }


def test_map_latest_reactor_rows_missing_profile() -> None:
    post_id = uuid.uuid4()
    mapped = _map_latest_reactor_rows([_sql_row(post_id, profile_user_id=None)])
    reaction, profile, university = mapped[post_id][0]
    assert profile is None
    assert university is None
    assert reaction.post_id == post_id
    assert reaction.reaction_type == ReactionType.like


def test_map_latest_reactor_rows_preserves_order_and_types() -> None:
    post_id = uuid.uuid4()
    profile_user_id = uuid.uuid4()
    t1 = datetime(2026, 1, 3, tzinfo=timezone.utc)
    t2 = datetime(2026, 1, 2, tzinfo=timezone.utc)
    mapped = _map_latest_reactor_rows(
        [
            _sql_row(post_id, reaction_type="celebrate", created_at=t2, profile_user_id=profile_user_id),
            _sql_row(post_id, reaction_type="like", created_at=t1, profile_user_id=profile_user_id),
        ]
    )
    rows = mapped[post_id]
    assert len(rows) == 2
    assert rows[0][0].reaction_type == ReactionType.celebrate
    assert rows[1][0].reaction_type == ReactionType.like

    grouped = build_post_reactions_from_rows(rows)
    assert len(grouped.LIKE) == 1
    assert len(grouped.CELEBRATE) == 1
    assert grouped.LIKE[0].first_name == "Ada"
    assert grouped.LIKE[0].reaction_type == "LIKE"
    assert grouped.LIKE[0].reacted_at == t1


def test_map_latest_reactor_rows_formats_profile_fields() -> None:
    post_id = uuid.uuid4()
    profile_user_id = uuid.uuid4()
    mapped = _map_latest_reactor_rows(
        [_sql_row(post_id, profile_user_id=profile_user_id)]
    )
    reaction, profile, _ = mapped[post_id][0]
    assert isinstance(profile, LatestReactorProfileRow)
    assert profile.user_id == profile_user_id
    assert isinstance(reaction, LatestReactorReactionRow)


@pytest.mark.asyncio
async def test_fetch_latest_reactors_for_posts_single_sql_round_trip() -> None:
    post_id = uuid.uuid4()
    mapping = MagicMock()
    mapping.mappings.return_value.all.return_value = [
        _sql_row(post_id, profile_user_id=uuid.uuid4())
    ]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=mapping)

    result = await fetch_latest_reactors_for_posts(db, [post_id], per_type_limit=3)

    assert db.execute.await_count == 1
    assert post_id in result
    assert len(result[post_id]) == 1


@pytest.mark.asyncio
async def test_fetch_latest_reactors_for_posts_empty_ids() -> None:
    db = AsyncMock()
    result = await fetch_latest_reactors_for_posts(db, [])
    assert result == {}
    db.execute.assert_not_called()
