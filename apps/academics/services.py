from __future__ import annotations

from datetime import datetime, timezone
from string import ascii_uppercase
from typing import Any, Iterable
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.academics.repositories import (
    get_catalog_record,
    not_found_message,
    parse_catalog_id,
)
from apps.academics.schemas import (
    AcademicCatalogType,
    AcademicInterestAdminItem,
    CatalogNameItem,
    CountryAdminItem,
    UniversityAdminItem,
)
from apps.profiles.db_models.academic_interests_db_model import AcademicInterest
from apps.profiles.db_models.country_db_model import Country
from apps.profiles.db_models.major_db_model import Major
from apps.profiles.db_models.minor_db_model import Minor
from apps.profiles.db_models.university_db_model import University
from apps.profiles.normalization import (
    normalize_major_minor,
    normalize_named_program_list,
)
from common.enums import CatalogAddedBy, InterestAddedBy
from common.exceptions import ApiError
from common.pagination import paginate_or_all


def _serialize_catalog_row(row) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "isActive": bool(row.is_active),
        "createdAt": row.created_at.isoformat() if getattr(row, "created_at", None) else None,
        "updatedAt": row.updated_at.isoformat() if getattr(row, "updated_at", None) else None,
    }


def _serialize_interest(row: AcademicInterest) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "majorId": str(row.major_id) if row.major_id is not None else None,
        "minorId": str(row.minor_id) if row.minor_id is not None else None,
        "educationLevelId": (
            str(row.education_level_id) if row.education_level_id is not None else None
        ),
        "isActive": bool(row.is_active),
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


def _serialize_university(row: University) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "slug": row.slug,
        "countryId": str(row.country_id),
        "major": row.major,
        "minor": row.minor,
        "academicProgram": row.academic_program,
        "isActive": bool(row.is_active),
        "website": row.website,
    }


def _serialize_country(row: Country) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "isoCode": row.iso_code,
        "isActive": bool(row.is_active),
    }


def _bulk_summary(
    *,
    created: list[dict[str, Any]],
    existing: list[dict[str, Any]],
    failed: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the shared bulk-processing response.

    ``items`` contains only records created (or successfully updated, for PATCH)
    in this request. ``already_existing`` contains records that already existed
    and were not newly inserted.
    """
    return {
        "total": len(created) + len(existing) + len(failed),
        "created": len(created),
        "existing": len(existing),
        "failed": len(failed),
        "items": created,
        "already_existing": existing,
        "failures": failed,
    }


_BULK_ENTITY_PLURALS = {
    "major": "majors",
    "minor": "minors",
    "interest": "interests",
    "university": "universities",
    "country": "countries",
}


def build_bulk_create_message(
    *,
    entity: str,
    created: int,
    existing: int,
    failed: int,
) -> str:
    """Build admin bulk-create message from outcome counts.

    Example: ``3 interests added successfully, 2 interests failed to create,
    4 interests already exists``
    """
    plural = _BULK_ENTITY_PLURALS[entity]
    parts: list[str] = []
    if created:
        parts.append(f"{created} {plural} added successfully")
    if failed:
        parts.append(f"{failed} {plural} failed to create")
    if existing:
        parts.append(f"{existing} {plural} already exists")
    if not parts:
        return f"No {plural} processed"
    return ", ".join(parts)


def _to_named_program_dicts(value: list | None) -> list[dict] | None:
    if value is None:
        return None
    normalized = normalize_named_program_list(value)
    if normalized is None:
        return None
    result: list[dict] = []
    for item in normalized:
        if isinstance(item, dict):
            result.append(item)
        else:
            result.append({"name": str(item)})
    return result


def _interest_combo_key(major_id: int, minor_id: int | None, name: str) -> tuple:
    return (major_id, minor_id, name.lower())


async def _load_catalog_by_lower_name(model, names: Iterable[str], db: AsyncSession) -> dict[str, Any]:
    unique_names = [name for name in {n.lower() for n in names if n}]
    if not unique_names:
        return {}
    rows = list(
        (
            await db.execute(
                select(model).where(func.lower(model.name).in_(unique_names))
            )
        )
        .scalars()
        .all()
    )
    return {row.name.lower(): row for row in rows}


def _catalog_row_from_ref(ref: int | str, by_id: dict[int, Any], by_name: dict[str, Any]):
    if isinstance(ref, int):
        return by_id.get(ref)
    normalized = normalize_major_minor(str(ref))
    if not normalized:
        return None
    return by_name.get(normalized.lower())


async def _ensure_named_catalog_refs(
    model,
    refs: Iterable[int | str | None],
    db: AsyncSession,
    *,
    added_by_field: str,
    added_by: str,
    create_missing: bool = True,
) -> tuple[dict[int, Any], dict[str, Any], set[int]]:
    ids: set[int] = set()
    names: list[str] = []
    for ref in refs:
        if ref is None:
            continue
        if isinstance(ref, int):
            ids.add(ref)
            continue
        normalized = normalize_major_minor(str(ref))
        if normalized:
            names.append(normalized)

    by_id: dict[int, Any] = {}
    if ids:
        rows = (
            await db.execute(select(model).where(model.id.in_(list(ids))))
        ).scalars().all()
        by_id = {row.id: row for row in rows}
    missing_ids = ids - set(by_id.keys())

    by_name = await _load_catalog_by_lower_name(model, names, db)
    if create_missing:
        to_create = []
        queued: set[str] = set()
        for name in names:
            key = name.lower()
            if key in by_name or key in queued:
                continue
            queued.add(key)
            row = model(
                name=name,
                is_active=True,
                **{added_by_field: added_by},
            )
            to_create.append(row)
            by_name[key] = row

        if to_create:
            db.add_all(to_create)
            await db.flush()
            for row in to_create:
                if getattr(row, "id", None) is not None:
                    by_id[row.id] = row

    return by_id, by_name, missing_ids


def _preferred_iso_code(name: str) -> str:
    letters = "".join(ch for ch in name.upper() if ch.isalpha())
    padded = (letters + "X" * 2)[:2]
    return padded or "XX"


def _allocate_iso_code(name: str, used: set[str]) -> str:
    preferred = _preferred_iso_code(name)
    if preferred not in used:
        return preferred
    for first in ascii_uppercase:
        for second in ascii_uppercase:
            code = f"{first}{second}"
            if code not in used:
                return code
    raise ApiError("Unable to allocate country iso_code")


def _country_row_from_ref(
    ref: UUID | str,
    by_id: dict[UUID, Any],
    by_name: dict[str, Any],
):
    if isinstance(ref, UUID):
        return by_id.get(ref)
    key = " ".join(str(ref).split()).lower()
    return by_name.get(key)


async def _ensure_country_refs(
    refs: Iterable[UUID | str],
    db: AsyncSession,
    *,
    create_missing: bool = True,
) -> tuple[dict[UUID, Any], dict[str, Any], set[UUID]]:
    ids: set[UUID] = set()
    names: list[str] = []
    for ref in refs:
        if isinstance(ref, UUID):
            ids.add(ref)
            continue
        collapsed = " ".join(str(ref).split())
        if collapsed:
            names.append(collapsed)

    by_id: dict[UUID, Any] = {}
    if ids:
        rows = (
            await db.execute(select(Country).where(Country.id.in_(list(ids))))
        ).scalars().all()
        by_id = {row.id: row for row in rows}
    missing_ids = ids - set(by_id.keys())

    by_name = await _load_catalog_by_lower_name(Country, names, db)
    if create_missing:
        used_iso: set[str] = set()
        if names:
            used_iso = {
                str(code).upper()
                for code in (await db.execute(select(Country.iso_code))).scalars().all()
                if code
            }

        to_create = []
        queued: set[str] = set()
        for name in names:
            key = name.lower()
            if key in by_name or key in queued:
                continue
            queued.add(key)
            iso = _allocate_iso_code(name, used_iso)
            used_iso.add(iso)
            row = Country(name=name, iso_code=iso, is_active=True)
            to_create.append(row)
            by_name[key] = row

        if to_create:
            db.add_all(to_create)
            await db.flush()
            for row in to_create:
                by_id[row.id] = row

    return by_id, by_name, missing_ids


def _iter_program_refs(values: list | None) -> list[int | str]:
    if not values:
        return []
    refs: list[int | str] = []
    for item in values:
        if isinstance(item, bool):
            continue
        if isinstance(item, int):
            refs.append(item)
            continue
        if isinstance(item, dict):
            raw_id = item.get("id")
            if isinstance(raw_id, int) and not isinstance(raw_id, bool):
                refs.append(raw_id)
                continue
            name = item.get("name")
            if name:
                refs.append(str(name))
            continue
        text = " ".join(str(item).split())
        if text:
            refs.append(text)
    return refs


def _resolved_program_names(
    values: list | None,
    by_id: dict[int, Any],
    by_name: dict[str, Any],
    *,
    field: str,
) -> tuple[list[str] | None, str | None]:
    if values is None:
        return None, None
    names: list[str] = []
    for ref in _iter_program_refs(values):
        row = _catalog_row_from_ref(ref, by_id, by_name)
        if row is None:
            return None, f"{field} not found"
        names.append(row.name)
    return names, None


async def bulk_create_majors(items: list[Any], db: AsyncSession) -> dict[str, Any]:
    return await _bulk_create_named_catalog(Major, items, db)


async def bulk_create_minors(items: list[Any], db: AsyncSession) -> dict[str, Any]:
    return await _bulk_create_named_catalog(Minor, items, db)


def _coerce_catalog_name_items(
    items: list[Any],
) -> tuple[list[CatalogNameItem], list[dict[str, Any]]]:
    """Validate major/minor name rows; invalid items soft-fail into ``failures``."""
    valid: list[CatalogNameItem] = []
    failed: list[dict[str, Any]] = []
    for raw in items:
        if isinstance(raw, CatalogNameItem):
            valid.append(raw)
            continue
        if not isinstance(raw, dict):
            failed.append({"name": "", "reason": "invalid item"})
            continue
        try:
            valid.append(CatalogNameItem.model_validate(raw))
        except ValidationError as exc:
            failed.append(
                {
                    "name": _bulk_failure_name(raw),
                    "reason": _bulk_item_validation_reason(exc),
                }
            )
    return valid, failed


async def _bulk_create_named_catalog(
    model,
    items: list[Any],
    db: AsyncSession,
) -> dict[str, Any]:
    created: list[dict[str, Any]] = []
    existing: list[dict[str, Any]] = []
    items, failed = _coerce_catalog_name_items(items)
    seen_in_payload: dict[str, CatalogNameItem] = {}
    normalized_items: list[CatalogNameItem] = []

    for item in items:
        catalog_name = normalize_major_minor(item.name)
        if not catalog_name:
            failed.append({"name": item.name, "reason": "name cannot be empty"})
            continue
        key = catalog_name.lower()
        if key in seen_in_payload:
            existing.append({"name": catalog_name, "duplicateInRequest": True})
            continue
        seen_in_payload[key] = item
        normalized_items.append(
            CatalogNameItem(name=catalog_name, is_active=item.is_active)
        )

    existing_by_name = await _load_catalog_by_lower_name(
        model,
        [item.name for item in normalized_items],
        db,
    )

    to_create = []
    for item in normalized_items:
        current = existing_by_name.get(item.name.lower())
        if current is not None:
            existing.append(_serialize_catalog_row(current))
            continue
        row = model(
            name=item.name,
            is_active=item.is_active,
            **(
                {"major_added_by": CatalogAddedBy.admin.value}
                if model is Major
                else {"minor_added_by": CatalogAddedBy.admin.value}
                if model is Minor
                else {}
            ),
        )
        to_create.append(row)

    if to_create:
        db.add_all(to_create)
        try:
            await db.flush()
        except IntegrityError as exc:
            await db.rollback()
            raise ApiError("Duplicate catalog name") from exc
        created.extend(_serialize_catalog_row(row) for row in to_create)

    await db.commit()
    return _bulk_summary(created=created, existing=existing, failed=failed)


def _bulk_failure_name(raw: Any) -> str:
    if isinstance(raw, dict):
        name = raw.get("name")
    else:
        name = getattr(raw, "name", None)
    if name is None:
        return ""
    return str(name).strip()


def _bulk_item_validation_reason(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "invalid data"
    first = errors[0]
    loc = first.get("loc") or ()
    field = str(loc[-1]) if loc else "value"
    msg = str(first.get("msg") or "invalid data")
    if msg.lower().startswith("value error, "):
        msg = msg[13:]
    lowered = msg.lower()
    err_type = str(first.get("type") or "")
    if (
        "field required" in lowered
        or "cannot be empty" in lowered
        or err_type in {"missing", "string_too_short"}
        or "at least 1 character" in lowered
    ):
        return f"{field} cannot be blank"
    return msg


def _raw_iso_code(raw: Any) -> str | None:
    if isinstance(raw, dict):
        value = raw.get("iso_code", raw.get("isoCode"))
    else:
        value = getattr(raw, "iso_code", None)
    if value is None:
        return None
    text = str(value).strip().upper()
    return text or None


def _coerce_academic_interest_items(
    items: list[Any],
) -> tuple[list[AcademicInterestAdminItem], list[dict[str, Any]]]:
    valid: list[AcademicInterestAdminItem] = []
    failed: list[dict[str, Any]] = []
    for raw in items:
        if isinstance(raw, AcademicInterestAdminItem):
            item = raw
        elif isinstance(raw, dict):
            try:
                item = AcademicInterestAdminItem.model_validate(raw)
            except ValidationError as exc:
                failed.append(
                    {
                        "name": _bulk_failure_name(raw),
                        "reason": _bulk_item_validation_reason(exc),
                    }
                )
                continue
        else:
            failed.append({"name": "", "reason": "invalid item"})
            continue

        if item.major is None:
            failed.append({"name": item.name, "reason": "major cannot be blank"})
            continue
        valid.append(item)
    return valid, failed


def _coerce_country_items(
    items: list[Any],
) -> tuple[list[CountryAdminItem], list[dict[str, Any]]]:
    valid: list[CountryAdminItem] = []
    failed: list[dict[str, Any]] = []
    for raw in items:
        if isinstance(raw, CountryAdminItem):
            valid.append(raw)
            continue
        if not isinstance(raw, dict):
            failed.append({"name": "", "reason": "invalid item"})
            continue
        try:
            valid.append(CountryAdminItem.model_validate(raw))
        except ValidationError as exc:
            failure: dict[str, Any] = {
                "name": _bulk_failure_name(raw),
                "reason": _bulk_item_validation_reason(exc),
            }
            iso_code = _raw_iso_code(raw)
            if iso_code is not None:
                failure["isoCode"] = iso_code
            failed.append(failure)
    return valid, failed


def _coerce_university_items(
    items: list[Any],
) -> tuple[list[UniversityAdminItem], list[dict[str, Any]]]:
    valid: list[UniversityAdminItem] = []
    failed: list[dict[str, Any]] = []
    for raw in items:
        if isinstance(raw, UniversityAdminItem):
            valid.append(raw)
            continue
        if not isinstance(raw, dict):
            failed.append({"name": "", "reason": "invalid item"})
            continue
        try:
            valid.append(UniversityAdminItem.model_validate(raw))
        except ValidationError as exc:
            failed.append(
                {
                    "name": _bulk_failure_name(raw),
                    "reason": _bulk_item_validation_reason(exc),
                }
            )
    return valid, failed


async def bulk_create_academic_interests(
    items: list[Any],
    db: AsyncSession,
) -> dict[str, Any]:
    created: list[dict[str, Any]] = []
    existing: list[dict[str, Any]] = []
    items, failed = _coerce_academic_interest_items(items)

    major_by_id, major_by_name, missing_major_ids = await _ensure_named_catalog_refs(
        Major,
        [item.major for item in items],
        db,
        added_by_field="major_added_by",
        added_by=CatalogAddedBy.admin.value,
        create_missing=False,
    )
    minor_by_id, minor_by_name, missing_minor_ids = await _ensure_named_catalog_refs(
        Minor,
        [item.minor for item in items],
        db,
        added_by_field="minor_added_by",
        added_by=CatalogAddedBy.admin.value,
        create_missing=False,
    )

    resolved: list[tuple[AcademicInterestAdminItem, int, int | None]] = []
    for item in items:
        if isinstance(item.major, int) and item.major in missing_major_ids:
            failed.append({"name": item.name, "reason": "major not found"})
            continue
        major_row = _catalog_row_from_ref(item.major, major_by_id, major_by_name)
        if major_row is None:
            failed.append({"name": item.name, "reason": "major not found"})
            continue

        minor_id = None
        if item.minor is not None:
            if isinstance(item.minor, int) and item.minor in missing_minor_ids:
                failed.append({"name": item.name, "reason": "minor not found"})
                continue
            minor_row = _catalog_row_from_ref(item.minor, minor_by_id, minor_by_name)
            if minor_row is None:
                failed.append({"name": item.name, "reason": "minor not found"})
                continue
            minor_id = int(minor_row.id)

        resolved.append((item, int(major_row.id), minor_id))

    major_ids = {major_id for _, major_id, _ in resolved}
    combo_stmt = select(AcademicInterest).where(AcademicInterest.major_id.in_(list(major_ids)))
    existing_combos = {
        _interest_combo_key(row.major_id, row.minor_id, row.name): row
        for row in (await db.execute(combo_stmt)).scalars().all()
        if row.major_id is not None
    } if major_ids else {}

    seen_in_payload: set[tuple] = set()
    to_create: list[AcademicInterest] = []

    for item, major_id, minor_id in resolved:
        combo = _interest_combo_key(major_id, minor_id, item.name)

        if combo in seen_in_payload:
            existing.append(
                {
                    "name": item.name,
                    "majorId": str(major_id),
                    "minorId": str(minor_id) if minor_id is not None else None,
                    "duplicateInRequest": True,
                }
            )
            continue
        seen_in_payload.add(combo)

        current = existing_combos.get(combo)
        if current is not None:
            existing.append(_serialize_interest(current))
            continue

        row = AcademicInterest(
            name=item.name,
            major_id=major_id,
            minor_id=minor_id,
            education_level_id=None,
            is_active=item.is_active,
            interest_added_by=InterestAddedBy.admin.value,
        )
        to_create.append(row)
        existing_combos[combo] = row

    if to_create:
        db.add_all(to_create)
        try:
            await db.flush()
        except IntegrityError as exc:
            await db.rollback()
            raise ApiError("Duplicate academic interest combination") from exc
        created.extend(_serialize_interest(row) for row in to_create)

    await db.commit()
    return _bulk_summary(created=created, existing=existing, failed=failed)


async def bulk_create_universities(
    items: list[Any],
    db: AsyncSession,
) -> dict[str, Any]:
    created: list[dict[str, Any]] = []
    existing: list[dict[str, Any]] = []
    items, failed = _coerce_university_items(items)

    country_by_id, country_by_name, missing_country_ids = await _ensure_country_refs(
        [item.country for item in items],
        db,
        create_missing=False,
    )
    major_refs = [ref for item in items for ref in _iter_program_refs(item.major)]
    minor_refs = [ref for item in items for ref in _iter_program_refs(item.minor)]
    major_by_id, major_by_name, missing_major_ids = await _ensure_named_catalog_refs(
        Major,
        major_refs,
        db,
        added_by_field="major_added_by",
        added_by=CatalogAddedBy.admin.value,
    )
    minor_by_id, minor_by_name, missing_minor_ids = await _ensure_named_catalog_refs(
        Minor,
        minor_refs,
        db,
        added_by_field="minor_added_by",
        added_by=CatalogAddedBy.admin.value,
    )

    slugs = {item.slug.lower() for item in items}
    existing_by_slug = {
        row.slug.lower(): row
        for row in (
            await db.execute(
                select(University).where(func.lower(University.slug).in_(list(slugs)))
            )
        ).scalars().all()
    } if slugs else {}

    seen_slugs: set[str] = set()
    to_create: list[University] = []

    for item in items:
        if isinstance(item.country, UUID) and item.country in missing_country_ids:
            failed.append({"name": item.name, "reason": "country not found"})
            continue
        country_row = _country_row_from_ref(item.country, country_by_id, country_by_name)
        if country_row is None:
            failed.append({"name": item.name, "reason": "country not found"})
            continue

        if any(isinstance(ref, int) and ref in missing_major_ids for ref in _iter_program_refs(item.major)):
            failed.append({"name": item.name, "reason": "major not found"})
            continue
        if any(isinstance(ref, int) and ref in missing_minor_ids for ref in _iter_program_refs(item.minor)):
            failed.append({"name": item.name, "reason": "minor not found"})
            continue

        major_names, major_error = _resolved_program_names(
            item.major, major_by_id, major_by_name, field="major"
        )
        if major_error:
            failed.append({"name": item.name, "reason": major_error})
            continue
        minor_names, minor_error = _resolved_program_names(
            item.minor, minor_by_id, minor_by_name, field="minor"
        )
        if minor_error:
            failed.append({"name": item.name, "reason": minor_error})
            continue

        slug_key = item.slug.lower()
        if slug_key in seen_slugs:
            existing.append({"name": item.name, "slug": item.slug, "duplicateInRequest": True})
            continue
        seen_slugs.add(slug_key)

        current = existing_by_slug.get(slug_key)

        payload_fields = {
            "name": item.name,
            "slug": item.slug,
            "country_id": country_row.id,
            "major": _to_named_program_dicts(major_names),
            "minor": _to_named_program_dicts(minor_names),
            "academic_program": _to_named_program_dicts(item.academic_program),
            "is_active": item.is_active,
            "website": item.website,
        }
        if current is not None:
            for field, value in payload_fields.items():
                setattr(current, field, value)
            db.add(current)
            existing.append(_serialize_university(current))
            continue

        row = University(**payload_fields)
        to_create.append(row)

    if to_create:
        db.add_all(to_create)
        try:
            await db.flush()
        except IntegrityError as exc:
            await db.rollback()
            raise ApiError("Duplicate university slug") from exc
        created.extend(_serialize_university(row) for row in to_create)

    await db.commit()
    return _bulk_summary(created=created, existing=existing, failed=failed)


def _is_valid_country_iso_code(iso_code: str) -> bool:
    return len(iso_code) == 2 and iso_code.isalpha()


async def bulk_create_countries(
    items: list[Any],
    db: AsyncSession,
) -> dict[str, Any]:
    created: list[dict[str, Any]] = []
    existing: list[dict[str, Any]] = []
    items, failed = _coerce_country_items(items)

    valid_items: list[CountryAdminItem] = []
    for item in items:
        if not _is_valid_country_iso_code(item.iso_code):
            failed.append(
                {
                    "name": item.name,
                    "isoCode": item.iso_code,
                    "reason": "ISO code format does not match",
                }
            )
            continue
        valid_items.append(item)

    iso_codes = {item.iso_code for item in valid_items}

    existing_by_iso = {
        row.iso_code.upper(): row
        for row in (
            await db.execute(select(Country).where(func.upper(Country.iso_code).in_(list(iso_codes))))
        ).scalars().all()
    } if iso_codes else {}

    seen_iso: set[str] = set()
    to_create: list[Country] = []

    for item in valid_items:
        if item.iso_code in seen_iso:
            existing.append({"name": item.name, "isoCode": item.iso_code, "duplicateInRequest": True})
            continue
        seen_iso.add(item.iso_code)

        current = existing_by_iso.get(item.iso_code)

        if current is not None:
            current.name = item.name
            current.iso_code = item.iso_code
            current.is_active = item.is_active
            db.add(current)
            existing.append(_serialize_country(current))
            continue

        row = Country(name=item.name, iso_code=item.iso_code, is_active=item.is_active)
        to_create.append(row)

    if to_create:
        db.add_all(to_create)
        try:
            await db.flush()
        except IntegrityError as exc:
            await db.rollback()
            raise ApiError("Duplicate country iso_code") from exc
        created.extend(_serialize_country(row) for row in to_create)

    await db.commit()
    return _bulk_summary(created=created, existing=existing, failed=failed)


def _serialize_soft_delete(catalog_type: AcademicCatalogType, row) -> dict[str, Any]:
    updated_at = getattr(row, "updated_at", None)
    return {
        "type": catalog_type.value,
        "id": str(row.id),
        "isActive": bool(row.is_active),
        "updatedAt": updated_at.isoformat() if updated_at else None,
    }


async def soft_delete_catalog_record(
    catalog_type: AcademicCatalogType,
    record_id: str,
    db: AsyncSession,
) -> dict[str, Any]:
    """Deactivate a catalog row in place. Never physically deletes or cascades."""
    parsed_id = parse_catalog_id(catalog_type, record_id)
    if parsed_id is None:
        raise ApiError(not_found_message(catalog_type))

    row = await get_catalog_record(db, catalog_type, parsed_id)
    if row is None:
        raise ApiError(not_found_message(catalog_type))

    row.is_active = False
    if catalog_type != AcademicCatalogType.country:
        row.updated_at = datetime.now(timezone.utc)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _serialize_soft_delete(catalog_type, row)


async def _list_active_catalog(
    model,
    *,
    query: str | None,
    page: int | None,
    page_size: int | None,
    sort: str | None = None,
    order: str | None = None,
    db: AsyncSession,
    added_by_field: str,
) -> dict:
    filters = [model.is_active == True]  # noqa: E712
    clean_query = (query or "").strip()
    if clean_query:
        filters.append(model.name.ilike(f"%{clean_query}%"))

    count_stmt = select(func.count()).select_from(model).where(*filters)

    sort_val = sort.value if hasattr(sort, "value") else sort
    order_val = order.value if hasattr(order, "value") else order

    sort_field = (sort_val or "").strip().lower()
    if sort_field == "created_at":
        order_dir = (order_val or "desc").strip().lower()
        col = getattr(model, "created_at", getattr(model, "updated_at", model.name))
        stmt = select(model).where(*filters).order_by(col.asc() if order_dir == "asc" else col.desc(), model.id.asc())
    elif order_val:
        order_dir = order_val.strip().lower()
        stmt = select(model).where(*filters).order_by(model.name.desc() if order_dir == "desc" else model.name.asc(), model.id.asc())
    else:
        stmt = select(model).where(*filters).order_by(model.name.asc())


    total_items = int((await db.execute(count_stmt)).scalar_one())
    if page is not None and page_size is not None:
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)

    rows = list((await db.execute(stmt)).scalars().all())
    items = [
        {
            "id": str(row.id),
            "name": row.name,
            added_by_field: getattr(row, added_by_field),
        }
        for row in rows
    ]
    return paginate_or_all(
        items,
        page,
        page_size,
        total_items=total_items,
    ).model_dump()


async def list_test_majors(
    query: str | None,
    page: int | None,
    page_size: int | None,
    db: AsyncSession,
    sort: str | None = None,
    order: str | None = None,
) -> dict:
    return await _list_active_catalog(
        Major,
        query=query,
        page=page,
        page_size=page_size,
        sort=sort,
        order=order,
        db=db,
        added_by_field="major_added_by",
    )


async def list_test_minors(
    query: str | None,
    page: int | None,
    page_size: int | None,
    db: AsyncSession,
    sort: str | None = None,
    order: str | None = None,
) -> dict:
    return await _list_active_catalog(
        Minor,
        query=query,
        page=page,
        page_size=page_size,
        sort=sort,
        order=order,
        db=db,
        added_by_field="minor_added_by",
    )


async def list_test_interests(
    *,
    major_id: int | None,
    minor_id: int | None,
    query: str | None,
    page: int | None,
    page_size: int | None,
    sort: str | None = None,
    order: str | None = None,
    db: AsyncSession,
) -> dict:
    if major_id is not None:
        major = (
            await db.execute(
                select(Major).where(Major.id == major_id, Major.is_active == True)  # noqa: E712
            )
        ).scalar_one_or_none()
        if major is None:
            raise ApiError("Major not found")

    if minor_id is not None:
        minor = (
            await db.execute(
                select(Minor).where(Minor.id == minor_id, Minor.is_active == True)  # noqa: E712
            )
        ).scalar_one_or_none()
        if minor is None:
            raise ApiError("Minor not found")

    filters = [AcademicInterest.is_active == True]  # noqa: E712
    if major_id is not None and minor_id is not None:
        # Match either catalog id so callers get the union of both scopes.
        filters.append(
            or_(
                AcademicInterest.major_id == major_id,
                AcademicInterest.minor_id == minor_id,
            )
        )
    elif major_id is not None:
        # All interests linked to the major, including rows with a minor set.
        filters.append(AcademicInterest.major_id == major_id)
    elif minor_id is not None:
        filters.append(AcademicInterest.minor_id == minor_id)

    clean_query = (query or "").strip()
    if clean_query:
        filters.append(AcademicInterest.name.ilike(f"%{clean_query}%"))

    sort_val = sort.value if hasattr(sort, "value") else sort
    order_val = order.value if hasattr(order, "value") else order

    sort_field = (sort_val or "").strip().lower()
    if sort_field == "created_at":
        order_dir = (order_val or "desc").strip().lower()
        order_clause = [
            AcademicInterest.created_at.asc() if order_dir == "asc" else AcademicInterest.created_at.desc(),
            AcademicInterest.id.asc(),
        ]
    elif order_val:
        order_dir = order_val.strip().lower()
        order_clause = [
            AcademicInterest.name.desc() if order_dir == "desc" else AcademicInterest.name.asc(),
            AcademicInterest.id.asc(),
        ]
    else:
        order_clause = [AcademicInterest.name.asc(), AcademicInterest.id.asc()]



    stmt = (
        select(AcademicInterest, Major.name, Minor.name)
        .outerjoin(Major, Major.id == AcademicInterest.major_id)
        .outerjoin(Minor, Minor.id == AcademicInterest.minor_id)
        .where(and_(*filters))
        .distinct()
        .order_by(*order_clause)
    )
    rows = list((await db.execute(stmt)).all())
    items = [
        {
            "id": str(interest.id),
            "name": interest.name,
            "majorName": major_name,
            "minorName": minor_name,
            "interest_added_by": interest.interest_added_by,
        }
        for interest, major_name, minor_name in rows
    ]
    return paginate_or_all(items, page, page_size).model_dump()



async def _find_catalog_by_name(model, name: str, db: AsyncSession):
    normalized = normalize_major_minor(name)
    if not normalized:
        return None
    return (
        await db.execute(
            select(model).where(func.lower(model.name) == normalized.lower())
        )
    ).scalar_one_or_none()


def _dedupe_interest_names(names: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for name in names:
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(name)
    return unique


def _serialize_user_interest(row: AcademicInterest) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "major_id": row.major_id,
        "minor_id": row.minor_id,
        "education_level_id": row.education_level_id,
        "interest_added_by": row.interest_added_by,
    }


def _serialize_user_major(row: Major) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "major_added_by": row.major_added_by,
    }


def _serialize_user_minor(row: Minor) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "minor_added_by": row.minor_added_by,
    }


async def _resolve_major_ref(major: int | str, db: AsyncSession) -> Major:
    """Resolve major by integer id or case-insensitive name. Creates row with user origin if name not found."""
    if isinstance(major, int):
        row = (
            await db.execute(select(Major).where(Major.id == major))
        ).scalar_one_or_none()
        if row is None:
            raise ApiError("Major not found")
        return row

    normalized = normalize_major_minor(str(major))
    if not normalized:
        raise ApiError("Major not found")
    row = await _find_catalog_by_name(Major, normalized, db)
    if row is not None:
        return row

    row = Major(
        name=normalized,
        is_active=True,
        major_added_by=CatalogAddedBy.user.value,
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        row = await _find_catalog_by_name(Major, normalized, db)
        if row is None:
            raise
    return row


async def _resolve_minor_ref(minor: int | str, db: AsyncSession) -> Minor:
    """Resolve minor by integer id or case-insensitive name. Creates row with user origin if name not found."""
    if isinstance(minor, int):
        row = (
            await db.execute(select(Minor).where(Minor.id == minor))
        ).scalar_one_or_none()
        if row is None:
            raise ApiError("Minor not found")
        return row

    normalized = normalize_major_minor(str(minor))
    if not normalized:
        raise ApiError("Minor not found")
    row = await _find_catalog_by_name(Minor, normalized, db)
    if row is not None:
        return row

    row = Minor(
        name=normalized,
        is_active=True,
        minor_added_by=CatalogAddedBy.user.value,
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        row = await _find_catalog_by_name(Minor, normalized, db)
        if row is None:
            raise
    return row


def _assert_minor_belongs_to_major(major: Major, minor: Minor) -> None:
    """Reject minors scoped to a different major when ownership metadata exists.

    The ``minors`` table has no ``major_id`` FK today (global catalog). If a minor
    row ever carries ``major_id``, enforce it here so we never silently pair a
    minor with the wrong major.
    """
    owned_by = getattr(minor, "major_id", None)
    if owned_by is not None and int(owned_by) != int(major.id):
        raise ApiError("Minor does not belong to the specified major")


async def create_user_academic_interests(
    *,
    major: int | str,
    minor: int | str | None,
    interests: list[str],
    db: AsyncSession,
) -> dict[str, Any]:
    major_row = await _resolve_major_ref(major, db)

    minor_row = None
    if minor is not None and minor != "":
        minor_row = await _resolve_minor_ref(minor, db)
        _assert_minor_belongs_to_major(major_row, minor_row)

    unique_names = _dedupe_interest_names(interests)
    major_id = int(major_row.id)
    minor_id = int(minor_row.id) if minor_row is not None else None

    name_filters = [
        func.lower(func.trim(AcademicInterest.name)) == name.lower()
        for name in unique_names
    ]
    existing_filters = [
        AcademicInterest.major_id == major_id,
        or_(*name_filters) if name_filters else AcademicInterest.id.is_(None),
    ]
    if minor_id is None:
        existing_filters.append(AcademicInterest.minor_id.is_(None))
    else:
        existing_filters.append(AcademicInterest.minor_id == minor_id)

    existing_rows = list(
        (await db.execute(select(AcademicInterest).where(and_(*existing_filters)))).scalars().all()
    )
    existing_by_name = {row.name.strip().lower(): row for row in existing_rows}

    created_rows: list[AcademicInterest] = []
    for name in unique_names:
        if existing_by_name.get(name.lower()) is not None:
            raise ApiError("Duplicate academic interest combination")
        row = AcademicInterest(
            name=name,
            major_id=major_id,
            minor_id=minor_id,
            education_level_id=None,
            is_active=True,
            interest_added_by=InterestAddedBy.user.value,
        )
        created_rows.append(row)
        existing_by_name[name.lower()] = row

    db.add_all(created_rows)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ApiError("Duplicate academic interest combination") from exc

    await db.commit()
    for row in created_rows:
        await db.refresh(row)
    await db.refresh(major_row)
    if minor_row is not None:
        await db.refresh(minor_row)

    return {
        "major": _serialize_user_major(major_row),
        "minor": _serialize_user_minor(minor_row) if minor_row is not None else None,
        "items": [_serialize_user_interest(row) for row in created_rows],
    }


async def patch_catalog_record(
    catalog_type: AcademicCatalogType,
    record_id: str,
    data: dict[str, Any],
    db: AsyncSession,
) -> dict[str, Any]:
    # 1. Parse and validate ID using the existing parse_catalog_id helper
    parsed_id = parse_catalog_id(catalog_type, record_id)
    if parsed_id is None:
        raise ApiError(not_found_message(catalog_type))

    # 2. Retrieve the existing catalog record
    row = await get_catalog_record(db, catalog_type, parsed_id)
    if row is None:
        raise ApiError(not_found_message(catalog_type))

    # 3. Apply validations and changes type-by-type
    if catalog_type == AcademicCatalogType.major:
        if "name" in data:
            proposed_name = data["name"]
            existing_with_name = (
                await db.execute(
                    select(Major).where(
                        func.lower(Major.name) == proposed_name.lower(),
                        Major.id != parsed_id,
                    )
                )
            ).scalar_one_or_none()
            if existing_with_name is not None:
                raise ApiError("Duplicate catalog name")
            row.name = proposed_name

        if "is_active" in data:
            row.is_active = data["is_active"]

    elif catalog_type == AcademicCatalogType.minor:
        if "name" in data:
            proposed_name = data["name"]
            existing_with_name = (
                await db.execute(
                    select(Minor).where(
                        func.lower(Minor.name) == proposed_name.lower(),
                        Minor.id != parsed_id,
                    )
                )
            ).scalar_one_or_none()
            if existing_with_name is not None:
                raise ApiError("Duplicate catalog name")
            row.name = proposed_name

        if "is_active" in data:
            row.is_active = data["is_active"]

    elif catalog_type == AcademicCatalogType.academic_interest:
        proposed_name = data.get("name", row.name)
        proposed_major_id = data["major_id"] if "major_id" in data else row.major_id
        proposed_minor_id = data["minor_id"] if "minor_id" in data else row.minor_id

        # Rule check: major_id is required for the resulting record
        if proposed_major_id is None:
            raise ApiError("major_id is required")

        # Validate major_id exists
        if "major_id" in data:
            major_exists = (
                await db.execute(select(Major).where(Major.id == proposed_major_id))
            ).scalar_one_or_none()
            if not major_exists:
                raise ApiError("major_id not found")

        # Validate minor_id exists
        if "minor_id" in data and proposed_minor_id is not None:
            minor_exists = (
                await db.execute(select(Minor).where(Minor.id == proposed_minor_id))
            ).scalar_one_or_none()
            if not minor_exists:
                raise ApiError("minor_id not found")

        # Check unique constraint on (name, major_id, minor_id)
        stmt = select(AcademicInterest).where(
            func.lower(AcademicInterest.name) == proposed_name.lower(),
            AcademicInterest.major_id == proposed_major_id,
            AcademicInterest.id != parsed_id,
        )
        if proposed_minor_id is None:
            stmt = stmt.where(AcademicInterest.minor_id.is_(None))
        else:
            stmt = stmt.where(AcademicInterest.minor_id == proposed_minor_id)

        existing_combo = (await db.execute(stmt)).scalar_one_or_none()
        if existing_combo is not None:
            raise ApiError("Duplicate academic interest combination")

        # Apply changes
        if "name" in data:
            row.name = data["name"]
        if "major_id" in data:
            row.major_id = data["major_id"]
        if "minor_id" in data:
            row.minor_id = data["minor_id"]
        if "is_active" in data:
            row.is_active = data["is_active"]

    elif catalog_type == AcademicCatalogType.university:
        proposed_slug = data.get("slug", row.slug)
        if "slug" in data:
            existing_with_slug = (
                await db.execute(
                    select(University).where(
                        func.lower(University.slug) == proposed_slug.lower(),
                        University.id != parsed_id,
                    )
                )
            ).scalar_one_or_none()
            if existing_with_slug is not None:
                raise ApiError("Duplicate university slug")

        if "country_id" in data:
            country_exists = (
                await db.execute(select(Country).where(Country.id == data["country_id"]))
            ).scalar_one_or_none()
            if not country_exists:
                raise ApiError("country_id not found")

        # Apply changes
        if "name" in data:
            row.name = data["name"]
        if "slug" in data:
            row.slug = data["slug"]
        if "country_id" in data:
            row.country_id = data["country_id"]
        if "website" in data:
            row.website = data["website"]
        if "is_active" in data:
            row.is_active = data["is_active"]

        if "major" in data:
            row.major = _to_named_program_dicts(data["major"])
        if "minor" in data:
            row.minor = _to_named_program_dicts(data["minor"])
        if "academic_program" in data:
            row.academic_program = _to_named_program_dicts(data["academic_program"])

    elif catalog_type == AcademicCatalogType.country:
        if "iso_code" in data:
            proposed_iso_code = data["iso_code"]
            existing_with_iso = (
                await db.execute(
                    select(Country).where(
                        func.upper(Country.iso_code) == proposed_iso_code.upper(),
                        Country.id != parsed_id,
                    )
                )
            ).scalar_one_or_none()
            if existing_with_iso is not None:
                raise ApiError("Duplicate country iso_code")
            row.iso_code = proposed_iso_code

        if "name" in data:
            row.name = data["name"]
        if "is_active" in data:
            row.is_active = data["is_active"]

    # 4. Set updated_at timestamp. Country uses the PostgreSQL now() onupdate.
    if catalog_type != AcademicCatalogType.country:
        row.updated_at = datetime.now(timezone.utc)

    # 5. Save and refresh
    db.add(row)
    await db.commit()
    await db.refresh(row)

    # Serialize. PATCH updates a single existing row; place the updated record in
    # ``items`` (counted as created for the shared bulk shape). Do NOT classify a
    # successful update as already_existing.
    if catalog_type in (AcademicCatalogType.major, AcademicCatalogType.minor):
        serialized = _serialize_catalog_row(row)
    elif catalog_type == AcademicCatalogType.academic_interest:
        serialized = _serialize_interest(row)
    elif catalog_type == AcademicCatalogType.university:
        serialized = _serialize_university(row)
    elif catalog_type == AcademicCatalogType.country:
        serialized = _serialize_country(row)
    else:
        raise ApiError(f"Invalid catalog type: {catalog_type}")

    return _bulk_summary(created=[serialized], existing=[], failed=[])

