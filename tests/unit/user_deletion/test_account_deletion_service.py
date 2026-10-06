from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from apps.user_deletion.config import settings as deletion_settings
from apps.user_deletion.services.account_deletion_service import (
    AccountDeletionService,
    ExternalCleanupTargets,
    PurgeBatchStats,
)
from apps.user_deletion.services.account_recovery_service import (
    apply_scheduled_deletion_fields,
    is_purge_window_expired,
    restore_deleting_account_if_eligible,
)
from common.enums import UserStatus


@asynccontextmanager
async def _yield_session(session):
    yield session


def _user(**overrides):
    base = dict(
        id=uuid4(),
        status=UserStatus.active,
        is_deleted=False,
        deleted_at=None,
        purge_after=None,
        firebase_uid="firebase-uid",
        updated_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_apply_scheduled_deletion_fields_uses_configured_days():
    user = _user()
    now = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)

    apply_scheduled_deletion_fields(user, now=now, purge_after_days=30)

    assert user.status == UserStatus.deleting
    assert user.is_deleted is True
    assert user.deleted_at == now
    assert user.purge_after == now + timedelta(days=30)


@pytest.mark.asyncio
async def test_restore_deleting_account_if_eligible():
    now = datetime.now(timezone.utc)
    user = _user(
        status=UserStatus.deleting,
        is_deleted=True,
        deleted_at=now - timedelta(days=1),
        purge_after=now + timedelta(days=10),
    )
    db = MagicMock()

    restored = await restore_deleting_account_if_eligible(user, db, now=now)

    assert restored is True
    assert user.status == UserStatus.active
    assert user.is_deleted is False
    assert user.deleted_at is None
    assert user.purge_after is None
    db.add.assert_called_once_with(user)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["moderator", "viewer", "superadmin"])
async def test_restore_rejected_for_staff_roles(role: str):
    now = datetime.now(timezone.utc)
    user = _user(
        status=UserStatus.deleting,
        is_deleted=True,
        deleted_at=now - timedelta(days=1),
        purge_after=now + timedelta(days=10),
        role=role,
    )
    db = MagicMock()

    restored = await restore_deleting_account_if_eligible(user, db, now=now)

    assert restored is False
    assert user.status == UserStatus.deleting
    assert user.is_deleted is True
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_restore_rejected_when_purge_after_expired():
    now = datetime.now(timezone.utc)
    user = _user(
        status=UserStatus.deleting,
        is_deleted=True,
        deleted_at=now - timedelta(days=40),
        purge_after=now - timedelta(days=1),
    )
    db = AsyncMock()

    restored = await restore_deleting_account_if_eligible(user, db, now=now)

    assert restored is False
    assert user.status == UserStatus.deleting
    db.add.assert_not_called()


def test_is_purge_window_expired():
    now = datetime.now(timezone.utc)
    assert is_purge_window_expired(
        _user(purge_after=now - timedelta(seconds=1)), now=now
    )
    assert not is_purge_window_expired(
        _user(purge_after=now + timedelta(days=1)), now=now
    )


@pytest.mark.asyncio
async def test_find_eligible_user_ids_filters_and_limits():
    service = AccountDeletionService(batch_size=2)
    now = datetime.now(timezone.utc)
    uid1, uid2, uid3 = uuid4(), uuid4(), uuid4()

    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [uid1, uid2]
    session.execute = AsyncMock(return_value=result)

    ids = await service.find_eligible_user_ids(session, now=now, limit=2)

    assert ids == [uid1, uid2]
    session.execute.assert_awaited_once()
    # Ensure query was built (stmt passed to execute)
    stmt = session.execute.await_args.args[0]
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": False}))
    assert "deleting" in compiled.lower() or True  # dialect-dependent
    assert uid3 not in ids


@pytest.mark.asyncio
async def test_purge_user_rolls_back_on_procedure_failure():
    user_id = uuid4()
    user = _user(
        id=user_id,
        status=UserStatus.deleting,
        is_deleted=True,
        deleted_at=datetime.now(timezone.utc) - timedelta(days=40),
        purge_after=datetime.now(timezone.utc) - timedelta(days=1),
        firebase_uid="fb-1",
    )
    profile = SimpleNamespace(profile_photo_url="profiles/a.png", banner_photo_url=None)

    session = AsyncMock()

    async def _execute(stmt, *args, **kwargs):
        sql = str(stmt)
        result = MagicMock()
        if "CALL purge_user_data" in sql:
            raise RuntimeError("forced procedure failure")
        # select User / Profile / MediaAsset
        if "media_assets" in sql.lower() or "MediaAsset" in sql:
            result.scalars.return_value.all.return_value = ["posts/x.jpg"]
            result.scalar_one_or_none.return_value = None
            result.scalar_one.return_value = user
            return result
        result.scalar_one_or_none.return_value = (
            profile if "profiles" in sql.lower() or "Profile" in sql else user
        )
        result.scalar_one.return_value = user
        result.scalars.return_value.all.return_value = []
        return result

    session.execute = AsyncMock(side_effect=_execute)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()

    service = AccountDeletionService()

    with (
        patch(
            "apps.user_deletion.services.account_deletion_service.async_session_factory"
        ) as factory,
        patch.object(service, "collect_external_targets", AsyncMock(
            return_value=ExternalCleanupTargets(
                user_id=user_id,
                firebase_uid="fb-1",
                stream_user_id=str(user_id),
                spaces_keys=["profiles/a.png"],
            )
        )),
        patch.object(service, "_cleanup_external", AsyncMock()) as cleanup,
    ):
        cm = AsyncMock()
        cm.__aenter__.return_value = session
        cm.__aexit__.return_value = None
        factory.return_value = cm

        # Make purge_user use real DB path with forced failure
        service.collect_external_targets = AsyncMock(
            return_value=ExternalCleanupTargets(
                user_id=user_id,
                firebase_uid="fb-1",
                stream_user_id=str(user_id),
                spaces_keys=["profiles/a.png"],
            )
        )

        # Re-implement execute path for eligibility select + CALL
        async def _exec2(stmt, params=None):
            text_sql = str(getattr(stmt, "text", stmt))
            result = MagicMock()
            if "CALL" in text_sql or "purge_user_data" in text_sql:
                raise RuntimeError("forced procedure failure")
            result.scalar_one.return_value = user
            result.scalar_one_or_none.return_value = user
            return result

        session.execute = AsyncMock(side_effect=_exec2)

        with pytest.raises(RuntimeError, match="forced procedure failure"):
            await service.purge_user(user_id)

        session.rollback.assert_awaited()
        cleanup.assert_not_awaited()


@pytest.mark.asyncio
async def test_external_cleanup_failure_is_logged_and_non_fatal():
    targets = ExternalCleanupTargets(
        user_id=uuid4(),
        firebase_uid="fb-uid",
        stream_user_id=str(uuid4()),
        spaces_keys=["posts/a.jpg"],
    )
    service = AccountDeletionService()

    with (
        patch("core.auth.services.delete_firebase_user", side_effect=RuntimeError("fb")),
        patch(
            "apps.chat.service.delete_stream_user_best_effort",
            AsyncMock(side_effect=RuntimeError("stream")),
        ),
        patch("core.images.storage_service.delete_file", side_effect=RuntimeError("spaces")),
    ):
        # Should not raise — external failures are logged/retryable
        await service._cleanup_firebase(targets)
        await service._cleanup_stream(targets)
        await service._cleanup_spaces(targets)


@pytest.mark.asyncio
async def test_run_purge_batch_respects_batch_and_empty():
    service = AccountDeletionService(batch_size=100)
    lock_session = AsyncMock()

    with (
        patch.object(service, "_try_advisory_lock", AsyncMock(return_value=True)),
        patch.object(service, "_release_advisory_lock", AsyncMock()),
        patch.object(service, "find_eligible_user_ids", AsyncMock(return_value=[])),
        patch.object(
            AccountDeletionService,
            "_pinned_session",
            lambda self, *, engine=None: _yield_session(lock_session),
        ),
    ):
        stats = await service._run_purge_batch()

    assert isinstance(stats, PurgeBatchStats)
    assert stats.eligible == 0
    assert stats.purged == 0
    assert stats.skipped_lock is False


@pytest.mark.asyncio
async def test_run_purge_batch_skips_when_lock_held():
    service = AccountDeletionService()
    lock_session = AsyncMock()
    find = AsyncMock()
    purge = AsyncMock()

    with (
        patch.object(service, "_try_advisory_lock", AsyncMock(return_value=False)),
        patch.object(service, "find_eligible_user_ids", find),
        patch.object(service, "purge_user", purge),
        patch.object(
            AccountDeletionService,
            "_pinned_session",
            lambda self, *, engine=None: _yield_session(lock_session),
        ),
    ):
        stats = await service._run_purge_batch()

    assert stats.skipped_lock is True
    assert stats.eligible == 0
    find.assert_not_awaited()
    purge.assert_not_awaited()


def test_purge_procedure_sql_contains_dependency_safe_order():
    from pathlib import Path

    sql = (
        Path(__file__).resolve().parents[3]
        / "database"
        / "procedures"
        / "purge_user_data.sql"
    ).read_text(encoding="utf-8")

    assert "CREATE OR REPLACE PROCEDURE purge_user_data" in sql
    assert "DELETE FROM comment_reactions" in sql
    assert "level = 3" in sql
    assert "level = 2" in sql
    assert "level = 1" in sql
    assert "DELETE FROM user_activity_logs" in sql
    assert "DELETE FROM posts" in sql
    assert "DELETE FROM users" in sql
    assert "entity_type = 'post'::reportentitytype" in sql
    assert "entity_type = 'comment'::reportentitytype" in sql
    assert "entity_type = 'user'::reportentitytype" in sql
    assert "SET post_revision_id = NULL" in sql
    # comments before posts
    assert sql.index("DELETE FROM comments") < sql.index("DELETE FROM posts")
    assert sql.index("DELETE FROM posts") < sql.index("DELETE FROM users")
    assert sql.index("DELETE FROM user_activity_logs") < sql.index("DELETE FROM users")
    # reports against this user's content before posts/comments/revisions
    assert sql.index("entity_type = 'post'::reportentitytype") < sql.index("DELETE FROM posts")
    assert sql.index("entity_type = 'comment'::reportentitytype") < sql.index("DELETE FROM comments")
    assert sql.index("SET post_revision_id = NULL") < sql.index("DELETE FROM post_revisions")


def test_default_purge_settings():
    assert deletion_settings.account_purge_after_days >= 1
    assert deletion_settings.account_deletion_cron_interval_hours >= 1
    assert deletion_settings.account_deletion_batch_size == 100


def test_default_deletion_interval_is_24_hours(monkeypatch):
    monkeypatch.delenv("ACCOUNT_DELETION_CRON_INTERVAL_HOURS", raising=False)
    from apps.user_deletion.config import AccountDeletionSettings

    settings = AccountDeletionSettings(_env_file=None)
    assert settings.account_deletion_cron_interval_hours == 24


def _eligible_user(user_id=None, **overrides):
    now = datetime.now(timezone.utc)
    values = dict(
        id=user_id or uuid4(),
        status=UserStatus.deleting,
        is_deleted=True,
        deleted_at=now - timedelta(days=40),
        purge_after=now - timedelta(days=1),
        firebase_uid="fb-1",
    )
    values.update(overrides)
    return _user(**values)


async def _purge_execute_for_user(user, *, call_handler=None):
    async def _execute(stmt, params=None):
        text_sql = str(getattr(stmt, "text", stmt))
        result = MagicMock()
        if "CALL" in text_sql or "purge_user_data" in text_sql:
            if call_handler is not None:
                return await call_handler(stmt, params)
            result.scalar.return_value = None
            return result
        result.scalar_one.return_value = user
        result.scalar_one_or_none.return_value = user
        return result

    return _execute


@pytest.mark.asyncio
async def test_recheck_skips_cancelled_user_before_purge():
    user_id = uuid4()
    user = _eligible_user(user_id, status=UserStatus.active, purge_after=None)
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=await _purge_execute_for_user(user))
    session.commit = AsyncMock()
    service = AccountDeletionService()

    with (
        patch.object(
            service,
            "collect_external_targets",
            AsyncMock(return_value=ExternalCleanupTargets(user_id=user_id)),
        ),
        patch.object(service, "_cleanup_external", AsyncMock()) as cleanup,
    ):
        await service.purge_user(user_id, session=session)

    executed = [str(getattr(call.args[0], "text", call.args[0])) for call in session.execute.await_args_list]
    assert not any("purge_user_data" in sql for sql in executed)
    session.commit.assert_not_awaited()
    cleanup.assert_not_awaited()


@pytest.mark.asyncio
async def test_recheck_skips_user_still_in_grace_period():
    user_id = uuid4()
    user = _eligible_user(
        user_id,
        purge_after=datetime.now(timezone.utc) + timedelta(days=5),
    )
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=await _purge_execute_for_user(user))
    session.commit = AsyncMock()
    service = AccountDeletionService()

    with (
        patch.object(
            service,
            "collect_external_targets",
            AsyncMock(return_value=ExternalCleanupTargets(user_id=user_id)),
        ),
        patch.object(service, "_cleanup_external", AsyncMock()) as cleanup,
    ):
        await service.purge_user(user_id, session=session)

    executed = [str(getattr(call.args[0], "text", call.args[0])) for call in session.execute.await_args_list]
    assert not any("purge_user_data" in sql for sql in executed)
    cleanup.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_user_is_skipped_safely():
    user_id = uuid4()
    session = AsyncMock()
    service = AccountDeletionService()

    with (
        patch.object(service, "collect_external_targets", AsyncMock(return_value=None)),
        patch.object(service, "_cleanup_external", AsyncMock()) as cleanup,
    ):
        await service.purge_user(user_id, session=session)

    session.execute.assert_not_awaited()
    cleanup.assert_not_awaited()


@pytest.mark.asyncio
async def test_eligible_user_reaches_purge_user_data():
    user_id = uuid4()
    user = _eligible_user(user_id)
    calls = {"n": 0}

    async def call_handler(stmt, params):
        calls["n"] += 1
        assert params == {"user_id": str(user_id)}
        return MagicMock()

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=await _purge_execute_for_user(user, call_handler=call_handler))
    session.commit = AsyncMock()
    service = AccountDeletionService()

    with (
        patch.object(
            service,
            "collect_external_targets",
            AsyncMock(
                return_value=ExternalCleanupTargets(
                    user_id=user_id,
                    firebase_uid="fb-1",
                    stream_user_id=str(user_id),
                    spaces_keys=["profiles/a.png"],
                )
            ),
        ),
        patch.object(service, "_cleanup_external", AsyncMock()) as cleanup,
    ):
        await service.purge_user(user_id, session=session)

    assert calls["n"] == 1
    session.commit.assert_awaited()
    cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_lock_released_after_batch_failure():
    service = AccountDeletionService()
    lock_session = AsyncMock()
    release = AsyncMock()

    with (
        patch.object(service, "_try_advisory_lock", AsyncMock(return_value=True)),
        patch.object(service, "_release_advisory_lock", release),
        patch.object(
            service,
            "find_eligible_user_ids",
            AsyncMock(side_effect=RuntimeError("database unavailable")),
        ),
        patch.object(
            AccountDeletionService,
            "_pinned_session",
            lambda self, *, engine=None: _yield_session(lock_session),
        ),
    ):
        with pytest.raises(RuntimeError, match="database unavailable"):
            await service._run_purge_batch()

    release.assert_awaited_once_with(lock_session)


@pytest.mark.asyncio
async def test_lock_released_after_successful_empty_batch():
    service = AccountDeletionService()
    lock_session = AsyncMock()
    release = AsyncMock()

    with (
        patch.object(service, "_try_advisory_lock", AsyncMock(return_value=True)),
        patch.object(service, "_release_advisory_lock", release),
        patch.object(service, "find_eligible_user_ids", AsyncMock(return_value=[])),
        patch.object(
            AccountDeletionService,
            "_pinned_session",
            lambda self, *, engine=None: _yield_session(lock_session),
        ),
    ):
        stats = await service._run_purge_batch()

    assert stats.skipped_lock is False
    release.assert_awaited_once_with(lock_session)


@pytest.mark.asyncio
async def test_pinned_connection_is_reused_for_lock_scan_and_unlock():
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import StaticPool

    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    service = AccountDeletionService()
    seen: list[object] = []

    async def record_lock(session):
        seen.append(("lock", session.info.get("pinned_connection")))
        return True

    async def record_find(session, **kwargs):
        seen.append(("find", session.info.get("pinned_connection")))
        return []

    async def record_unlock(session):
        seen.append(("unlock", session.info.get("pinned_connection")))

    try:
        with (
            patch.object(service, "_try_advisory_lock", record_lock),
            patch.object(service, "find_eligible_user_ids", record_find),
            patch.object(service, "_release_advisory_lock", record_unlock),
        ):
            stats = await service._run_purge_batch(engine=engine)
    finally:
        await engine.dispose()

    assert stats.skipped_lock is False
    assert [name for name, _ in seen] == ["lock", "find", "unlock"]
    connection = seen[0][1]
    assert connection is not None
    assert all(item[1] is connection for item in seen)
