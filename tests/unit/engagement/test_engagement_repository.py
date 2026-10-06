from __future__ import annotations

import uuid

import pytest

from apps.engagement.repositories.engagement_repository import (
    PostEngagementFlags,
    fetch_post_engagement_flags,
)
from common.enums import ReactionType
from tests.unit.conftest import FakeScalarResult


@pytest.mark.asyncio
async def test_fetch_post_engagement_flags_empty_post_ids(mock_db):
    db = mock_db()
    flags = await fetch_post_engagement_flags(db, uuid.uuid4(), [])
    assert flags == PostEngagementFlags.empty()


@pytest.mark.asyncio
async def test_fetch_post_engagement_flags_batches_reactions_reposts_and_bookmarks(mock_db, scalar_result):
    user_id = uuid.uuid4()
    post_a = uuid.uuid4()
    post_b = uuid.uuid4()
    post_c = uuid.uuid4()

    db = mock_db(
        FakeScalarResult(
            values=[
                (post_a, ReactionType.like, False, False),
                (post_b, None, False, True),
                (post_c, None, True, False),
            ]
        ),
    )

    flags = await fetch_post_engagement_flags(db, user_id, [post_a, post_b, post_c])

    assert flags.user_reaction_for(post_a) == ReactionType.like
    assert post_a in flags.liked_post_ids
    assert post_b in flags.reposted_post_ids
    assert post_c in flags.bookmarked_post_ids
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_fetch_post_engagement_flags_excludes_deleted_reposts(mock_db):
    post_id = uuid.uuid4()
    db = mock_db(
        FakeScalarResult(values=[(post_id, None, False, False)]),
    )
    flags = await fetch_post_engagement_flags(db, uuid.uuid4(), [post_id])
    assert post_id not in flags.reposted_post_ids


@pytest.mark.asyncio
async def test_fetch_post_engagement_flags_skips_reposts_without_profile(mock_db):
    post_id = uuid.uuid4()
    db = mock_db(
        FakeScalarResult(values=[(post_id, None, False, False)]),
    )
    flags = await fetch_post_engagement_flags(db, uuid.uuid4(), [post_id])
    assert flags.reposted_post_ids == frozenset()
    assert db.execute.await_count == 1
