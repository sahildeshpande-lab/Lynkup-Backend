from __future__ import annotations

from datetime import date, timedelta

import pytest

from apps.learningspotlight.services.cycle_service import (
    LearningSpotlightCycleService,
    get_cycle_day,
    get_spotlight_type,
)
from common.enums import SpotlightType


@pytest.mark.parametrize(
    ("offset_days", "expected_day", "expected_type"),
    [
        (0, 1, SpotlightType.leading_thinker),
        (1, 2, SpotlightType.country_perspective),
        (2, 3, SpotlightType.influential_research),
        (3, 4, SpotlightType.latest_research),
        (4, 5, SpotlightType.beyond_your_field),
        (5, 1, SpotlightType.leading_thinker),
    ],
)
def test_cycle_day_rotation_from_start_date(
    offset_days: int,
    expected_day: int,
    expected_type: SpotlightType,
) -> None:
    """Case 5: +0..+5 days from cycle start maps through the 5-day rotation."""
    start = date(2026, 8, 23)
    today = start + timedelta(days=offset_days)

    day = get_cycle_day(start, today)
    assert day == expected_day
    assert get_spotlight_type(day) is expected_type

    service = LearningSpotlightCycleService(cycle_start_date=start)
    assert service.get_cycle_day(today) == expected_day
    assert service.get_spotlight_type(today) is expected_type


def test_get_spotlight_type_rejects_invalid_day() -> None:
    with pytest.raises(ValueError, match="cycle_day must be between 1 and 5"):
        get_spotlight_type(0)
