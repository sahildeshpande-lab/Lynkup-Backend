from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.profiles.services import profile_stats_service as svc


@pytest.mark.asyncio
async def test_adjust_counts_for_deleting_user_zeros_own_and_decrements_peers(mock_db):
    deleting_user_id = uuid.uuid4()
    peer_user_id = uuid.uuid4()
    deleting_profile = SimpleNamespace(id=uuid.uuid4(), user_id=deleting_user_id, posts_count=4)
    peer_profile = SimpleNamespace(id=uuid.uuid4(), user_id=peer_user_id, posts_count=2)
    deleting_stats = SimpleNamespace(profile_id=deleting_profile.id, connection_count=3)
    peer_stats = SimpleNamespace(profile_id=peer_profile.id, connection_count=5)
    connection = SimpleNamespace(
        user_low_id=deleting_user_id,
        user_high_id=peer_user_id,
        is_active=True,
    )

    deleting_profile_result = MagicMock()
    deleting_profile_result.scalar_one_or_none.return_value = deleting_profile
    connections_result = MagicMock()
    connections_result.scalars.return_value.all.return_value = [connection]
    peer_profile_result = MagicMock()
    peer_profile_result.scalar_one_or_none.return_value = peer_profile

    db = mock_db()
    db.execute = AsyncMock(
        side_effect=[
            deleting_profile_result,
            connections_result,
            peer_profile_result,
        ]
    )

    async def _get_or_create(_db, profile_id):
        if profile_id == deleting_profile.id:
            return deleting_stats
        if profile_id == peer_profile.id:
            return peer_stats
        raise AssertionError(f"Unexpected profile_id {profile_id}")

    with patch.object(svc, "get_or_create_profile_stats", AsyncMock(side_effect=_get_or_create)):
        await svc.adjust_counts_for_deleting_user(db, deleting_user_id)

    assert deleting_profile.posts_count == 0
    assert deleting_stats.connection_count == 0
    assert peer_stats.connection_count == 4
    assert connection.is_active is False
    db.add.assert_called_with(connection)
