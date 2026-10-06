from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from apps.user_deletion import cron as deletion_cron


@pytest.mark.asyncio
async def test_process_expired_account_deletions_logs_stats():
    stats = SimpleNamespace(eligible=2, purged=1, failed=0, skipped_lock=False)

    with patch(
        "apps.user_deletion.cron.AccountDeletionService.run_purge_batch",
        new=AsyncMock(return_value=stats),
    ) as run_batch:
        await deletion_cron.process_expired_account_deletions()

    run_batch.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_expired_account_deletions_swallows_errors():
    with patch(
        "apps.user_deletion.cron.AccountDeletionService.run_purge_batch",
        new=AsyncMock(side_effect=RuntimeError("db down")),
    ):
        await deletion_cron.process_expired_account_deletions()
