from __future__ import annotations

from datetime import date, datetime

GRADUATION_DATE_FORMAT = "%d-%m-%Y"
GRADUATION_DATE_ISO_FORMAT = "%Y-%m-%d"


def format_graduation_date(value: date | None) -> str | None:
    """Serialize graduation date for API responses (DD-MM-YYYY)."""
    if value is None:
        return None
    return value.strftime(GRADUATION_DATE_FORMAT)


def parse_graduation_date(value: object) -> date | None:
    """Parse graduation date from API input (DD-MM-YYYY, ISO fallback)."""
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not isinstance(value, str):
        raise ValueError("graduationDate must be a date string in DD-MM-YYYY format")

    cleaned = value.strip()
    if not cleaned:
        return None

    for fmt in (GRADUATION_DATE_FORMAT, GRADUATION_DATE_ISO_FORMAT):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue

    raise ValueError("graduationDate must be a valid date in DD-MM-YYYY format")
