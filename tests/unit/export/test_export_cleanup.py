from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from unittest.mock import AsyncMock, patch

import pytest

from apps.export.cleanup import cleanup_expired_exports
from apps.export.enums import DataExportStatus
from apps.export.models import DataExportRequest
from apps.export.service import DataExportService
from apps.export.storage import LocalExportStorage
from tests.unit.conftest import FakeScalarResult


@pytest.mark.asyncio
async def test_cleanup_expired_exports_callable(export_tmp_path: Path):
    export_id = uuid4()
    storage = LocalExportStorage(base_path=export_tmp_path)
    key = f"exports/{export_id}.zip"
    storage.save(key, b"zip")
    record = DataExportRequest(
        id=export_id,
        user_id=uuid4(),
        status=DataExportStatus.completed,
        storage_key=key,
        download_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    service = DataExportService(storage=storage)

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.execute = AsyncMock(return_value=FakeScalarResult(values=[record]))
    session.add = lambda *_a, **_k: None
    session.commit = AsyncMock()

    with patch("apps.export.cleanup.async_session_factory", return_value=session):
        cleaned = await cleanup_expired_exports(service=service)

    assert cleaned == 1
    assert record.status == DataExportStatus.expired
    assert record.storage_key is None
    assert not storage.exists(key)
