from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from apps.user_deletion.services.account_deletion_service import (
    AccountDeletionService,
    _ACCOUNT_DELETION_LOCK_KEY,
)


def _postgres_async_url() -> str | None:
    from core.database.config import settings

    url = settings.async_database_url
    if url.startswith("postgresql"):
        return url
    return None


async def _backend_pid(session: AsyncSession) -> int:
    return (await session.execute(text("SELECT pg_backend_pid()"))).scalar_one()


async def _try_lock(session: AsyncSession) -> bool:
    result = await session.execute(
        text("SELECT pg_try_advisory_lock(:key)"),
        {"key": _ACCOUNT_DELETION_LOCK_KEY},
    )
    return bool(result.scalar())


async def _unlock(session: AsyncSession) -> None:
    await session.execute(
        text("SELECT pg_advisory_unlock(:key)"),
        {"key": _ACCOUNT_DELETION_LOCK_KEY},
    )


@pytest.mark.asyncio
async def test_postgres_advisory_lock_contention_and_connection_pinning():
    pg_url = _postgres_async_url()
    if pg_url is None:
        pytest.skip("PostgreSQL not available")

    engine = create_async_engine(pg_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            await conn.commit()
    except Exception:
        await engine.dispose()
        pytest.skip("PostgreSQL not available")

    service = AccountDeletionService()
    pids: list[int] = []

    async def record_lock(session):
        pids.append(await _backend_pid(session))
        return await AccountDeletionService._try_advisory_lock(service, session)

    async def record_find(session, **kwargs):
        pids.append(await _backend_pid(session))
        return []

    async def record_unlock(session):
        pids.append(await _backend_pid(session))
        await AccountDeletionService._release_advisory_lock(service, session)

    try:
        with (
            patch.object(service, "_try_advisory_lock", record_lock),
            patch.object(service, "find_eligible_user_ids", record_find),
            patch.object(service, "_release_advisory_lock", record_unlock),
            patch.object(service, "purge_user", AsyncMock()),
        ):
            stats = await service._run_purge_batch(engine=engine)
        assert stats.skipped_lock is False
        assert len(pids) == 3
        assert pids[0] == pids[1] == pids[2]

        held = asyncio.Event()
        release = asyncio.Event()

        async def holder():
            async with engine.connect() as connection:
                async with AsyncSession(bind=connection, expire_on_commit=False) as session:
                    acquired = await _try_lock(session)
                    assert acquired is True
                    held.set()
                    await release.wait()
                    await _unlock(session)

        async def contender():
            await held.wait()
            with (
                patch.object(service, "find_eligible_user_ids", AsyncMock(return_value=[])),
                patch.object(service, "purge_user", AsyncMock()) as purge,
            ):
                result = await service._run_purge_batch(engine=engine)
                purge.assert_not_awaited()
            release.set()
            return result

        holder_task = asyncio.create_task(holder())
        contender_stats = await contender()
        await holder_task

        assert contender_stats.skipped_lock is True
        assert contender_stats.eligible == 0
        assert contender_stats.purged == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_lock_released_after_failure_so_next_batch_can_acquire():
    pg_url = _postgres_async_url()
    if pg_url is None:
        pytest.skip("PostgreSQL not available")

    engine = create_async_engine(pg_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            await conn.commit()
    except Exception:
        await engine.dispose()
        pytest.skip("PostgreSQL not available")

    service = AccountDeletionService()
    try:
        with (
            patch.object(
                service,
                "find_eligible_user_ids",
                AsyncMock(side_effect=RuntimeError("scan failed")),
            ),
            patch.object(service, "purge_user", AsyncMock()),
        ):
            with pytest.raises(RuntimeError, match="scan failed"):
                await service._run_purge_batch(engine=engine)

        with (
            patch.object(service, "find_eligible_user_ids", AsyncMock(return_value=[])),
            patch.object(service, "purge_user", AsyncMock()) as purge,
        ):
            stats = await service._run_purge_batch(engine=engine)
            assert stats.skipped_lock is False
            purge.assert_not_awaited()
    finally:
        await engine.dispose()
