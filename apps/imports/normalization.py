from __future__ import annotations

import re
import unicodedata
from collections import Counter
from typing import Any, Iterable

from pydantic import ValidationError
from rapidfuzz import fuzz, process

_NON_ALNUM_HYPHEN_RE = re.compile(r"[^a-z0-9]+")
_MULTI_HYPHEN_RE = re.compile(r"-{2,}")


def slugify_university_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", name or "")
    text = text.encode("ascii", "ignore").decode("ascii").strip().lower()
    text = _NON_ALNUM_HYPHEN_RE.sub("-", text)
    text = _MULTI_HYPHEN_RE.sub("-", text).strip("-")
    return text[:255]


TRUE_VALUES = {"true", "1", "yes", "y"}
FALSE_VALUES = {"false", "0", "no", "n"}


def is_blank(value: Any) -> bool:
    if value is None:
        return True
    try:
        import pandas as pd

        if pd.isna(value):
            return True
    except ImportError:
        pass
    except (TypeError, ValueError):
        pass
    if isinstance(value, str) and not value.strip():
        return True
    return False


def unwrap_cell(value: Any) -> Any:
    if is_blank(value):
        return None
    if hasattr(value, "item") and not isinstance(value, (bytes, str, dict, list)):
        try:
            value = value.item()
        except (ValueError, AttributeError):
            pass
    if is_blank(value):
        return None
    return value


def normalize_text(value: Any) -> str | None:
    value = unwrap_cell(value)
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        text = str(int(value))
    else:
        text = str(value).strip()
    return text or None


def normalize_int(value: Any) -> int | None:
    value = unwrap_cell(value)
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("invalid integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        raise ValueError("invalid integer")
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
        return int(text)
    if "." in text:
        number = float(text)
        if number.is_integer():
            return int(number)
    raise ValueError("invalid integer")


def normalize_bool(value: Any, *, default: bool | None = True) -> bool | None:
    value = unwrap_cell(value)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value == 1:
            return True
        if value == 0:
            return False
        raise ValueError("invalid boolean")
    text = str(value).strip().lower()
    if text in TRUE_VALUES:
        return True
    if text in FALSE_VALUES:
        return False
    raise ValueError("invalid boolean")


def column_map(raw: dict[str, Any]) -> dict[str, Any]:
    return {str(key).strip().lower(): value for key, value in raw.items()}


def pick(raw: dict[str, Any], *names: str) -> Any:
    lookup = column_map(raw)
    for name in names:
        key = name.strip().lower()
        if key in lookup:
            return lookup[key]
    return None


def validation_reason(exc: ValidationError, *, fallback: str = "Invalid data") -> str:
    errors = exc.errors()
    if not errors:
        return fallback
    first = errors[0]
    loc = first.get("loc") or ()
    field = loc[-1] if loc else "value"
    msg = str(first.get("msg") or fallback)
    if msg.lower().startswith("value error, "):
        msg = msg[13:]
    field_name = str(field)
    if "required" in msg.lower() or "cannot be empty" in msg.lower():
        labels = {
            "name": "Name is required",
            "slug": "Slug is required",
            "iso_code": "Code is required",
            "code": "Code is required",
            "country": "Country is required",
            "country_id": "Country is required",
            "major": "Major is required",
            "major_id": "Major is required",
            "website": "Website is required",
        }
        if field_name in labels:
            return labels[field_name]
    if field_name == "iso_code" and "2-letter" in msg:
        return "Code must be a 2-letter code"
    if field_name in {
        "name",
        "slug",
        "iso_code",
        "country",
        "country_id",
        "major",
        "major_id",
        "minor",
        "minor_id",
    }:
        return msg[0].upper() + msg[1:] if msg else fallback
    return msg


def parse_named_list(value: Any) -> list | None:
    value = unwrap_cell(value)
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, dict):
        return [value]
    text = str(value).strip()
    if not text:
        return None
    if text[0] in "[{":
        import json

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            return [parsed]
        if parsed is not None:
            return [parsed]
    return [part.strip() for part in text.split(",") if part.strip()] or None


CATALOG_FUZZY_CUTOFF = 88.0
CATALOG_FUZZY_MIN_LEN = 5


def match_existing_name(query: str, existing: dict[str, str]) -> str | None:
    """Return a canonical existing name for ``query``, or None if nothing is close enough."""
    if not query or not existing:
        return None
    key = query.lower()
    if key in existing:
        return existing[key]
    if len(key) < CATALOG_FUZZY_MIN_LEN:
        return None
    match = process.extractOne(
        key,
        list(existing.keys()),
        scorer=fuzz.WRatio,
        score_cutoff=CATALOG_FUZZY_CUTOFF,
    )
    if match is None:
        return None
    return existing[match[0]]


def resolve_catalog_names(
    requested: Iterable[str],
    existing: dict[str, str],
) -> dict[str, str]:
    """Map each requested name (lowercased key) to a canonical catalog name.

    Existing catalog names win, including misspellings. Remaining names that look
    like each other share one canonical value (most frequent, then longest).
    """
    requested_list = [name for name in requested if name]
    counts = Counter(name.lower() for name in requested_list)
    first_index: dict[str, int] = {}
    resolved: dict[str, str] = {}
    unmatched: list[str] = []

    for index, name in enumerate(requested_list):
        key = name.lower()
        if key in first_index:
            continue
        first_index[key] = index
        matched = match_existing_name(name, existing)
        if matched is not None:
            resolved[key] = matched
            continue
        unmatched.append(name)

    remaining = list(unmatched)
    while remaining:
        seed = remaining[0]
        cluster = [seed]
        cluster_lookup = {seed.lower(): seed}
        rest: list[str] = []
        for other in remaining[1:]:
            if match_existing_name(other, cluster_lookup) is not None:
                cluster.append(other)
                cluster_lookup[other.lower()] = other
            else:
                rest.append(other)
        canonical = max(
            cluster,
            key=lambda item: (
                counts[item.lower()],
                len(item),
                -first_index[item.lower()],
            ),
        )
        for item in cluster:
            resolved[item.lower()] = canonical
        remaining = rest

    return resolved
