from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from apps.feed.db_models import Post
from apps.feed.services import post_service as ps
from common.enums import PostState


def _post(*, moderator_id=None):
    return Post(
        id=uuid.uuid4(),
        author_user_id=uuid.uuid4(),
        content={"caption": "Test"},
        state=PostState.published,
        moderator_id=moderator_id,
        is_moderator_reviewed=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_repair_no_op_when_nothing_needs_repair(monkeypatch):
    superadmin_id = uuid.uuid4()
    db = AsyncMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [])))

    monkeypatch.setattr(ps, "_fetch_superadmin_user_ids", AsyncMock(return_value=[superadmin_id]))
    assign_rr = AsyncMock()
    monkeypatch.setattr(ps, "_assign_moderator_for_review", assign_rr)

    await ps._repair_unassigned_moderators(db, target_state=PostState.published)

    assign_rr.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_repair_reassigns_deleted_moderator_posts_to_superadmin(monkeypatch):
    superadmin_id = uuid.uuid4()
    deleted_mod_id = uuid.uuid4()
    post = _post(moderator_id=deleted_mod_id)

    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [post]))
    )

    monkeypatch.setattr(ps, "_fetch_superadmin_user_ids", AsyncMock(return_value=[superadmin_id]))
    monkeypatch.setattr(ps, "_assign_moderator_for_review", AsyncMock())
    sync_reports = AsyncMock()
    monkeypatch.setattr(
        "apps.report.repositories.report_repository.sync_open_report_moderator_for_post",
        sync_reports,
    )

    await ps._repair_unassigned_moderators(db, target_state=PostState.published)

    assert post.moderator_id == superadmin_id
    sync_reports.assert_awaited_once_with(
        db,
        post_id=post.id,
        moderator_id=superadmin_id,
    )
    db.add.assert_called_with(post)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_repair_null_moderator_uses_round_robin(monkeypatch):
    superadmin_id = uuid.uuid4()
    post = _post(moderator_id=None)

    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [post]))
    )

    monkeypatch.setattr(ps, "_fetch_superadmin_user_ids", AsyncMock(return_value=[superadmin_id]))
    assign_rr = AsyncMock()
    monkeypatch.setattr(ps, "_assign_moderator_for_review", assign_rr)

    await ps._repair_unassigned_moderators(db, target_state=PostState.published)

    assign_rr.assert_awaited_once_with(post, db)
    db.commit.assert_awaited_once()
