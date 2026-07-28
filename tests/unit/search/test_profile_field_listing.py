from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from apps.search.services import _list_distinct_profile_field


@pytest.mark.asyncio
async def test_list_distinct_profile_field_groups_case_variants(mock_db) -> None:
    db = mock_db()
    execute_results = [
        SimpleNamespace(scalar_one=lambda: 1),
        SimpleNamespace(all=lambda: [("ai",)]),
    ]
    db.execute = AsyncMock(side_effect=execute_results)

    result = await _list_distinct_profile_field(
        db,
        field_name="major",
        query=None,
        page=1,
        page_size=10,
    )

    assert result["items"] == [{"name": "ai"}]
    assert result["totalItems"] == 1
