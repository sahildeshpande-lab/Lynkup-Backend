"""Normalize free-text major/minor values for consistent storage and listing."""

from __future__ import annotations


def normalize_major_minor(value: str | None) -> str | None:
    """Title-case and collapse whitespace so case variants share one canonical form."""
    if value is None:
        return None
    normalized = " ".join(str(value).strip().split()).title()
    return normalized or None


def normalize_named_program_list(items: list | None) -> list | None:
    """Normalize ``name`` fields on university major/minor JSON entries and dedupe."""
    if items is None:
        return None

    combined: dict[str, dict | str] = {}
    for item in items:
        if isinstance(item, dict):
            raw_name = item.get("name") or item.get("title")
            normalized = normalize_major_minor(raw_name if raw_name is not None else None)
            if not normalized:
                continue
            entry = {**item, "name": normalized}
            if "title" in entry and entry.get("title") is not None:
                entry["title"] = normalized
            combined[normalized] = entry
        else:
            normalized = normalize_major_minor(str(item) if item is not None else None)
            if not normalized:
                continue
            combined[normalized] = normalized

    return list(combined.values())


def collect_normalized_program_names(
    *sources: str | None | list | None,
) -> list[str]:
    """Collect unique title-cased major/minor names from strings and named JSON lists."""
    combined: dict[str, str] = {}
    for source in sources:
        if source is None:
            continue
        if isinstance(source, str):
            normalized = normalize_major_minor(source)
            if normalized:
                combined[normalized] = normalized
            continue
        if isinstance(source, list):
            for item in source:
                if isinstance(item, dict):
                    raw_name = item.get("name") or item.get("title")
                    normalized = normalize_major_minor(
                        raw_name if raw_name is not None else None
                    )
                else:
                    normalized = normalize_major_minor(
                        str(item) if item is not None else None
                    )
                if normalized:
                    combined[normalized] = normalized
    return sorted(combined.keys())
