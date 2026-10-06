from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.academics.schemas import (
    AcademicInterestAdminItem,
    CatalogNameItem,
    CountryAdminItem,
    UniversityAdminItem,
)
from apps.academics.services import (
    _interest_combo_key,
    _load_catalog_by_lower_name,
    bulk_create_academic_interests,
    bulk_create_countries,
    bulk_create_majors,
    bulk_create_minors,
    bulk_create_universities,
)
from apps.imports.enums import ImportType
from apps.imports.file_parser import iter_records, require_columns
from apps.imports.normalization import (
    match_existing_name,
    normalize_text,
    parse_named_list,
    pick,
    resolve_catalog_names,
    slugify_university_name,
    validation_reason,
)
from apps.moderation.schemas import UpdateModerationWordsRequest
from apps.moderation.services.moderation_words_service import (
    _get_config,
    _normalize_words,
    update_moderation_words,
)
from apps.profiles.db_models.academic_interests_db_model import AcademicInterest
from apps.profiles.db_models.country_db_model import Country
from apps.profiles.db_models.major_db_model import Major
from apps.profiles.db_models.minor_db_model import Minor
from apps.profiles.db_models.university_db_model import University
from apps.profiles.normalization import normalize_major_minor


class ImportHandler(ABC):
    required_columns: tuple[str, ...] = ()

    def validate_headers(self, dataframe) -> None:
        require_columns(dataframe, self.required_columns)

    @abstractmethod
    async def process(
        self,
        dataframe,
        db: AsyncSession,
        *,
        actor_user_id=None,
        actor_role: str | None = None,
    ) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]]]:
        """Return successful_count, duplicate_rows, failed_rows."""


def _issue(row: int, reason: str) -> dict[str, Any]:
    return {"row": row, "reason": reason}


async def _load_named_rows(model, db: AsyncSession) -> dict[str, Any]:
    rows = list((await db.execute(select(model))).scalars().all())
    return {row.name.lower(): row for row in rows}


async def _ensure_named_catalog(
    *,
    model,
    bulk_create,
    requested_names: list[str],
    db: AsyncSession,
) -> dict[str, int]:
    if not requested_names:
        return {}
    unique_names: list[str] = []
    seen: set[str] = set()
    for name in requested_names:
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        unique_names.append(name)

    existing_rows = await _load_named_rows(model, db)
    existing_names = {key: row.name for key, row in existing_rows.items()}
    canonical_by_lower = resolve_catalog_names(unique_names, existing_names)

    id_by_lower = {key: int(row.id) for key, row in existing_rows.items() if row.id is not None}
    to_create: list[CatalogNameItem] = []
    queued: set[str] = set()
    for name in unique_names:
        canonical = canonical_by_lower[name.lower()]
        key = canonical.lower()
        if key in id_by_lower or key in queued:
            continue
        queued.add(key)
        to_create.append(CatalogNameItem(name=canonical))

    if to_create:
        result = await bulk_create(to_create, db)
        for item in result.get("items") or []:
            item_name = str(item["name"])
            id_by_lower[item_name.lower()] = int(item["id"])

    resolved_ids: dict[str, int] = {}
    for name in unique_names:
        canonical = canonical_by_lower[name.lower()]
        resolved_ids[name.lower()] = id_by_lower[canonical.lower()]
    return resolved_ids


class NamedCatalogImportHandler(ImportHandler):
    required_columns = ("name",)
    entity_label = "Record"
    model = Major
    bulk_create = staticmethod(bulk_create_majors)

    async def process(
        self,
        dataframe,
        db: AsyncSession,
        *,
        actor_user_id=None,
        actor_role: str | None = None,
    ) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]]]:
        _ = actor_user_id, actor_role
        duplicates: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        pending: list[tuple[int, CatalogNameItem]] = []
        seen: set[str] = set()
        label = self.entity_label

        for row_number, raw in iter_records(dataframe):
            name = normalize_text(pick(raw, "name"))
            if not name:
                failures.append(_issue(row_number, "Name is required"))
                continue
            try:
                item = CatalogNameItem(name=name)
            except ValidationError as exc:
                failures.append(_issue(row_number, validation_reason(exc, fallback="Name is required")))
                continue
            key = item.name.lower()
            if key in seen:
                duplicates.append(_issue(row_number, f"Duplicate {label.lower()} in file"))
                continue
            seen.add(key)
            pending.append((row_number, item))

        existing_by_name = await _load_catalog_by_lower_name(
            self.model,
            [item.name for _, item in pending],
            db,
        )
        valid_items: list[CatalogNameItem] = []
        for row_number, item in pending:
            if existing_by_name.get(item.name.lower()) is not None:
                duplicates.append(_issue(row_number, f"{label} already exists"))
                continue
            valid_items.append(item)

        if valid_items:
            result = await self.bulk_create(valid_items, db)
            return int(result.get("created") or 0), duplicates, failures
        return 0, duplicates, failures


class MajorImportHandler(NamedCatalogImportHandler):
    entity_label = "Major"
    model = Major
    bulk_create = staticmethod(bulk_create_majors)


class MinorImportHandler(NamedCatalogImportHandler):
    entity_label = "Minor"
    model = Minor
    bulk_create = staticmethod(bulk_create_minors)


class CountryImportHandler(ImportHandler):
    required_columns = ("name", "code")

    async def process(
        self,
        dataframe,
        db: AsyncSession,
        *,
        actor_user_id=None,
        actor_role: str | None = None,
    ) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]]]:
        _ = actor_user_id, actor_role
        duplicates: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        pending: list[tuple[int, CountryAdminItem]] = []
        seen: set[str] = set()

        for row_number, raw in iter_records(dataframe):
            name = normalize_text(pick(raw, "name"))
            code = normalize_text(pick(raw, "code", "iso_code", "isoCode"))
            if not name:
                failures.append(_issue(row_number, "Name is required"))
                continue
            if not code:
                failures.append(_issue(row_number, "Code is required"))
                continue
            try:
                item = CountryAdminItem(
                    name=name,
                    iso_code=code,
                )
            except ValidationError as exc:
                failures.append(_issue(row_number, validation_reason(exc)))
                continue
            # Soft-fail format lives in bulk_create_countries for the admin API;
            # imports still reject invalid codes here with a stable row reason.
            if len(item.iso_code) != 2 or not item.iso_code.isalpha():
                failures.append(_issue(row_number, "Code must be a 2-letter code"))
                continue
            key = item.iso_code
            if key in seen:
                duplicates.append(_issue(row_number, "Duplicate country in file"))
                continue
            seen.add(key)
            pending.append((row_number, item))

        iso_codes = {item.iso_code for _, item in pending}
        existing_by_iso = {
            row.iso_code.upper(): row
            for row in (
                await db.execute(
                    select(Country).where(func.upper(Country.iso_code).in_(list(iso_codes)))
                )
            ).scalars().all()
        } if iso_codes else {}

        valid_items: list[CountryAdminItem] = []
        for row_number, item in pending:
            if item.iso_code in existing_by_iso:
                duplicates.append(_issue(row_number, "Country already exists"))
                continue
            valid_items.append(item)

        if valid_items:
            result = await bulk_create_countries(valid_items, db)
            created = int(result.get("created") or 0)
            pending_by_iso = {item.iso_code: row_number for row_number, item in pending}
            for failure in result.get("failures") or []:
                iso = str(failure.get("isoCode") or "").upper()
                row_number = pending_by_iso.get(iso)
                if row_number is None:
                    continue
                reason = str(failure.get("reason") or "Invalid country data")
                if reason in (
                    "Format does not match",
                    "ISO code format does not match",
                ):
                    reason = "Code must be a 2-letter code"
                failures.append(_issue(row_number, reason))
            return created, duplicates, failures
        return 0, duplicates, failures


class UniversityImportHandler(ImportHandler):
    required_columns = ("name", "country", "major", "minor", "website")

    async def process(
        self,
        dataframe,
        db: AsyncSession,
        *,
        actor_user_id=None,
        actor_role: str | None = None,
    ) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]]]:
        _ = actor_user_id, actor_role
        duplicates: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        pending: list[tuple[int, UniversityAdminItem]] = []
        seen: set[str] = set()

        countries_by_name = await _load_named_rows(Country, db)
        country_names = {key: row.name for key, row in countries_by_name.items()}

        for row_number, raw in iter_records(dataframe):
            name = normalize_text(pick(raw, "name"))
            country_name = normalize_text(pick(raw, "country", "country_name"))
            if not name:
                failures.append(_issue(row_number, "Name is required"))
                continue
            if not country_name:
                failures.append(_issue(row_number, "Country is required"))
                continue
            matched_country_name = match_existing_name(country_name, country_names)
            if matched_country_name is None:
                failures.append(_issue(row_number, "country not found"))
                continue
            country = countries_by_name[matched_country_name.lower()]
            slug = slugify_university_name(name)
            if not slug:
                failures.append(_issue(row_number, "Unable to generate slug from name"))
                continue
            try:
                item = UniversityAdminItem(
                    name=name,
                    slug=slug,
                    country_id=country.id,
                    major=parse_named_list(pick(raw, "major")),
                    minor=parse_named_list(pick(raw, "minor")),
                    website=normalize_text(pick(raw, "website")),
                )
            except ValidationError as exc:
                failures.append(_issue(row_number, validation_reason(exc)))
                continue
            key = item.slug.lower()
            if key in seen:
                duplicates.append(_issue(row_number, "Duplicate university in file"))
                continue
            seen.add(key)
            pending.append((row_number, item))

        slugs = {item.slug.lower() for _, item in pending}
        existing_by_slug = {
            row.slug.lower(): row
            for row in (
                await db.execute(
                    select(University).where(func.lower(University.slug).in_(list(slugs)))
                )
            ).scalars().all()
        } if slugs else {}

        valid_items: list[UniversityAdminItem] = []
        for row_number, item in pending:
            if item.slug.lower() in existing_by_slug:
                duplicates.append(_issue(row_number, "University already exists"))
                continue
            valid_items.append(item)

        if valid_items:
            result = await bulk_create_universities(valid_items, db)
            created = int(result.get("created") or 0)
            return created, duplicates, failures
        return 0, duplicates, failures


class InterestImportHandler(ImportHandler):
    required_columns = ("name", "major")

    async def process(
        self,
        dataframe,
        db: AsyncSession,
        *,
        actor_user_id=None,
        actor_role: str | None = None,
    ) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]]]:
        _ = actor_user_id, actor_role
        duplicates: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        parsed: list[tuple[int, str, str, str | None]] = []

        for row_number, raw in iter_records(dataframe):
            name = normalize_text(pick(raw, "name"))
            if not name:
                failures.append(_issue(row_number, "Name is required"))
                continue
            major_name = normalize_major_minor(normalize_text(pick(raw, "major", "major_name")))
            if not major_name:
                failures.append(_issue(row_number, "Major is required"))
                continue
            minor_name = normalize_major_minor(normalize_text(pick(raw, "minor", "minor_name")))
            parsed.append((row_number, name, major_name, minor_name))

        major_ids = await _ensure_named_catalog(
            model=Major,
            bulk_create=bulk_create_majors,
            requested_names=[major_name for _, _, major_name, _ in parsed],
            db=db,
        )
        minor_ids = await _ensure_named_catalog(
            model=Minor,
            bulk_create=bulk_create_minors,
            requested_names=[minor_name for _, _, _, minor_name in parsed if minor_name],
            db=db,
        )

        pending: list[tuple[int, AcademicInterestAdminItem]] = []
        seen: set[tuple] = set()
        for row_number, name, major_name, minor_name in parsed:
            try:
                item = AcademicInterestAdminItem(
                    name=name,
                    major_id=major_ids[major_name.lower()],
                    minor_id=minor_ids[minor_name.lower()] if minor_name else None,
                )
            except ValidationError as exc:
                failures.append(_issue(row_number, validation_reason(exc)))
                continue
            combo = _interest_combo_key(item.major, item.minor, item.name)
            if combo in seen:
                duplicates.append(_issue(row_number, "Duplicate interest in file"))
                continue
            seen.add(combo)
            pending.append((row_number, item))

        resolved_major_ids = {item.major for _, item in pending}
        existing_combos = {
            _interest_combo_key(row.major_id, row.minor_id, row.name): row
            for row in (
                await db.execute(
                    select(AcademicInterest).where(
                        AcademicInterest.major_id.in_(list(resolved_major_ids))
                    )
                )
            ).scalars().all()
            if row.major_id is not None
        } if resolved_major_ids else {}

        valid_items: list[AcademicInterestAdminItem] = []
        for row_number, item in pending:
            combo = _interest_combo_key(item.major, item.minor, item.name)
            if combo in existing_combos:
                duplicates.append(_issue(row_number, "Interest already exists"))
                continue
            existing_combos[combo] = item
            valid_items.append(item)

        if valid_items:
            result = await bulk_create_academic_interests(valid_items, db)
            created = int(result.get("created") or 0)
            return created, duplicates, failures
        return 0, duplicates, failures


class ProfanityWordImportHandler(ImportHandler):
    required_columns = ("profanityWord",)

    async def process(
        self,
        dataframe,
        db: AsyncSession,
        *,
        actor_user_id=None,
        actor_role: str | None = None,
    ) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]]]:
        duplicates: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        new_words: list[str] = []
        seen: set[str] = set()

        config = await _get_config(db)
        existing_words = list(config.profanity_words or []) if config is not None else []
        existing_set = {word.lower() for word in existing_words}

        for row_number, raw in iter_records(dataframe):
            word = normalize_text(pick(raw, "profanityWord", "profanity_word", "word"))
            if not word:
                failures.append(_issue(row_number, "Profanity word is required"))
                continue
            normalized = word.strip().lower()
            if not normalized:
                failures.append(_issue(row_number, "Profanity word is required"))
                continue
            if normalized in seen:
                duplicates.append(_issue(row_number, "Duplicate profanity word in file"))
                continue
            seen.add(normalized)
            if normalized in existing_set:
                duplicates.append(_issue(row_number, "Profanity word already exists"))
                continue
            new_words.append(word)

        created = 0
        if new_words:
            merged = existing_words + _normalize_words(new_words)
            await update_moderation_words(
                UpdateModerationWordsRequest(profanityWords=merged),
                db,
                actor_user_id=actor_user_id,
                actor_role=actor_role,
            )
            created = len(_normalize_words(new_words))
        return created, duplicates, failures


HANDLERS: dict[ImportType, ImportHandler] = {
    ImportType.COUNTRY: CountryImportHandler(),
    ImportType.UNIVERSITY: UniversityImportHandler(),
    ImportType.MAJOR: MajorImportHandler(),
    ImportType.MINOR: MinorImportHandler(),
    ImportType.INTEREST: InterestImportHandler(),
    ImportType.PROFANITY_WORD: ProfanityWordImportHandler(),
}


def get_handler(import_type: ImportType) -> ImportHandler:
    return HANDLERS[import_type]
