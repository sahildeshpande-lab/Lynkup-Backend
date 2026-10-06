"""Global Learning Spotlight category cycle (system-wide, not per-user).

One spotlight category per calendar day; the five categories rotate forever.
New users join whatever day the global cycle is on — they do not restart at day 1.

This module is pure date math and category mapping. It is not connected to cron,
Semantic Scholar, or the database. Pass ``cycle_start_date`` and optional
``cycle_configuration`` from ``learning_recommendation_settings``.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from common.enums import SpotlightType

CYCLE_LENGTH_DAYS = 5

DEFAULT_CYCLE_ORDER: list[SpotlightType] = [
    SpotlightType.leading_thinker,
    SpotlightType.country_perspective,
    SpotlightType.influential_research,
    SpotlightType.latest_research,
    SpotlightType.beyond_your_field,
]

CYCLE_DAY_TO_SPOTLIGHT_TYPE: dict[int, SpotlightType] = {
    1: SpotlightType.leading_thinker,
    2: SpotlightType.country_perspective,
    3: SpotlightType.influential_research,
    4: SpotlightType.latest_research,
    5: SpotlightType.beyond_your_field,
}


def _as_utc_date(value: date | datetime | None) -> date:
    if value is None:
        return datetime.now(timezone.utc).date()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc).date()
        return value.astimezone(timezone.utc).date()
    return value


def get_cycle_day(
    cycle_start_date: date,
    today: date | datetime | None = None,
) -> int:
    """Return the global cycle day in ``1..CYCLE_LENGTH_DAYS``.

    ``cycle_day = ((today - cycle_start_date).days % 5) + 1``

    Uses calendar dates only (not wall-clock time).
    """
    if isinstance(cycle_start_date, datetime) or not isinstance(cycle_start_date, date):
        raise TypeError("cycle_start_date must be a date, not datetime")

    current = _as_utc_date(today)
    delta_days = (current - cycle_start_date).days
    return (delta_days % CYCLE_LENGTH_DAYS) + 1


def validate_cycle_configuration(
    config: dict[str, Any] | list[str] | list[SpotlightType] | None,
) -> list[SpotlightType]:
    """Validate and return the normalized 5-item list of SpotlightTypes."""
    if config is None:
        return list(DEFAULT_CYCLE_ORDER)

    raw_cycle: list[Any] | None = None
    if isinstance(config, dict):
        raw_cycle = config.get("cycle")
    elif isinstance(config, list):
        raw_cycle = config

    if raw_cycle is None:
        return list(DEFAULT_CYCLE_ORDER)

    if not isinstance(raw_cycle, list) or len(raw_cycle) != CYCLE_LENGTH_DAYS:
        raise ValueError(
            f"cycle_configuration must contain exactly {CYCLE_LENGTH_DAYS} spotlight types."
        )

    parsed_types: list[SpotlightType] = []
    for item in raw_cycle:
        if isinstance(item, SpotlightType):
            parsed_types.append(item)
        elif isinstance(item, str):
            try:
                parsed_types.append(SpotlightType(item))
            except ValueError:
                raise ValueError(f"Unknown spotlight type in cycle_configuration: {item}")
        else:
            raise ValueError(f"Invalid type in cycle_configuration: {item}")

    if len(set(parsed_types)) != CYCLE_LENGTH_DAYS:
        raise ValueError("cycle_configuration must not contain duplicate spotlight types.")

    required_set = set(SpotlightType)
    if set(parsed_types) != required_set:
        raise ValueError(
            f"cycle_configuration must contain all supported spotlight types: {required_set}"
        )

    return parsed_types


def get_spotlight_type(
    cycle_day: int,
    cycle_config: dict[str, Any] | list[str] | list[SpotlightType] | None = None,
) -> SpotlightType:
    """Map a cycle day (1-5) to its Learning Spotlight category based on configuration."""
    if cycle_day < 1 or cycle_day > CYCLE_LENGTH_DAYS:
        raise ValueError(
            f"cycle_day must be between 1 and {CYCLE_LENGTH_DAYS}, got {cycle_day}"
        )
    order = validate_cycle_configuration(cycle_config)
    return order[cycle_day - 1]


class LearningSpotlightCycleService:
    """Thin service wrapper around the global cycle helpers (testable / injectable)."""

    def __init__(
        self,
        *,
        cycle_start_date: date,
        cycle_configuration: dict[str, Any] | list[str] | None = None,
    ) -> None:
        if isinstance(cycle_start_date, datetime):
            raise TypeError("cycle_start_date must be a date, not datetime")
        self._cycle_start_date = cycle_start_date
        self._cycle_configuration = cycle_configuration

    @property
    def cycle_start_date(self) -> date:
        return self._cycle_start_date

    @property
    def cycle_configuration(self) -> dict[str, Any] | list[str] | None:
        return self._cycle_configuration

    def get_cycle_day(self, today: date | datetime | None = None) -> int:
        return get_cycle_day(self._cycle_start_date, today)

    def get_spotlight_type(
        self,
        today: date | datetime | None = None,
        *,
        cycle_day: int | None = None,
    ) -> SpotlightType:
        day = cycle_day if cycle_day is not None else self.get_cycle_day(today)
        return get_spotlight_type(day, self._cycle_configuration)


def parse_generated_at(value: object) -> datetime | None:
    """Parse generated_at field to UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def _normalize_spotlight_type(value: Any) -> str | None:
    if value is None:
        return None
    raw = getattr(value, "value", value)
    text = str(raw).strip()
    return text or None


def has_spotlight_for_cycle_day(
    snapshot: dict | None,
    *,
    cycle_day: int,
    today: date,
    spotlight_type: SpotlightType | str | None = None,
) -> bool:
    """True when the profile already has today's V2 spotlight for this day and type.

    Same calendar day and cycle day are not enough: if the admin remaps today's
    cycle day to a different spotlight type, the stored snapshot must be replaced.
    """
    if not isinstance(snapshot, dict):
        return False
    if snapshot.get("version") != 2:
        return False
    if snapshot.get("cycle_day") != cycle_day:
        return False
    generated_at = parse_generated_at(snapshot.get("generated_at"))
    if generated_at is None:
        return False
    if generated_at.date() != today:
        return False
    expected_type = _normalize_spotlight_type(spotlight_type)
    if expected_type is None:
        return True
    return _normalize_spotlight_type(snapshot.get("spotlight_type")) == expected_type
