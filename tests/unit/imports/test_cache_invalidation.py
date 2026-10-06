from __future__ import annotations

import pytest

from apps.imports.enums import ImportType
from core.cache.catalog import CATALOG_CACHE_KEYS, invalidate_catalog_cache
from tests.unit.imports.conftest import csv_bytes, run_import


@pytest.mark.asyncio
async def test_successful_import_invalidates_catalog_cache(db_session, monkeypatch) -> None:
    called: list[ImportType] = []

    async def _invalidate(import_type: ImportType) -> None:
        called.append(import_type)

    monkeypatch.setattr("apps.imports.services.invalidate_catalog_cache", _invalidate)
    result = await run_import(
        db_session,
        ImportType.MAJOR,
        "majors.csv",
        csv_bytes(["name"], [["History"]]),
    )
    assert result["successfulCount"] == 1
    assert called == [ImportType.MAJOR]


@pytest.mark.asyncio
async def test_zero_success_does_not_invalidate_cache(db_session, monkeypatch) -> None:
    called: list[ImportType] = []

    async def _invalidate(import_type: ImportType) -> None:
        called.append(import_type)

    monkeypatch.setattr("apps.imports.services.invalidate_catalog_cache", _invalidate)
    result = await run_import(
        db_session,
        ImportType.MAJOR,
        "majors.csv",
        csv_bytes(["name", "notes"], [["", "blank"]]),
    )
    assert result["successfulCount"] == 0
    assert called == []


@pytest.mark.asyncio
async def test_invalidate_catalog_cache_noops_without_redis() -> None:
    assert ImportType.PROFANITY_WORD in CATALOG_CACHE_KEYS
    await invalidate_catalog_cache(ImportType.COUNTRY)
