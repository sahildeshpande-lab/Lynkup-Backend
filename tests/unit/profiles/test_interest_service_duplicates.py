from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from apps.profiles.services.interest_service import _find_existing_academic_interest
from apps.search.services import create_academic_interest


@pytest.mark.asyncio
async def test_find_existing_academic_interest_scopes_to_education_level(mock_db, scalar_result) -> None:
    db = mock_db(scalar_result(None))

    result = await _find_existing_academic_interest("AI", db, education_level_id=1)

    assert result is None
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_academic_interest_allows_same_name_in_different_education_level(
    mock_db,
    scalar_result,
) -> None:
    education_level = SimpleNamespace(id=2, name="Masters", is_active=True)
    db = mock_db(scalar_result(education_level), scalar_result(None))

    async def set_generated_id(interest):
        interest.id = 11

    db.refresh.side_effect = set_generated_id

    result = await create_academic_interest("AI", 2, db)

    assert result == {
        "id": "11",
        "name": "AI",
        "educationLevelId": "2",
        "isActive": True,
    }
    db.add.assert_called_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_academic_interest_rejects_duplicate_in_same_education_level(
    mock_db,
    scalar_result,
) -> None:
    from common.exceptions import ApiError

    education_level = SimpleNamespace(id=1, name="Bachelors", is_active=True)
    existing = SimpleNamespace(id=3, name="AI", education_level_id=1)
    db = mock_db(scalar_result(education_level), scalar_result(existing))

    with pytest.raises(ApiError, match="Academic interest already exists"):
        await create_academic_interest("ai", 1, db)

    db.add.assert_not_called()
    db.commit.assert_not_awaited()
