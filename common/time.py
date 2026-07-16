from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def utc_now() -> datetime:
    """Current timestamp in UTC (timezone-aware)."""
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime) -> datetime:
    """Normalize a datetime to timezone-aware UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_timezone(tz_name: str) -> ZoneInfo:
    """Resolve a timezone name (e.g. Asia/Calcutta)."""
    return ZoneInfo(tz_name)


def now_in_timezone(tz_name: str) -> datetime:
    """Current timestamp in the given timezone."""
    return datetime.now(get_timezone(tz_name))


def calendar_day_bounds_utc(
    tz_name: str,
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """
    Return [start, end) of the current calendar day in ``tz_name``, as UTC datetimes.

    Useful for daily limits keyed to a local business timezone while storing UTC.
    """
    tz = get_timezone(tz_name)
    current = ensure_utc(now or utc_now()).astimezone(tz)
    start_local = datetime(current.year, current.month, current.day, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)
