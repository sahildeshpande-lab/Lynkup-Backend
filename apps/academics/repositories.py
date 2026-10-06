from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.academics.schemas import AcademicCatalogType
from apps.profiles.db_models.academic_interests_db_model import AcademicInterest
from apps.profiles.db_models.country_db_model import Country
from apps.profiles.db_models.major_db_model import Major
from apps.profiles.db_models.minor_db_model import Minor
from apps.profiles.db_models.university_db_model import University

# Allow-list only. Never derive a table/model from raw user input.
CATALOG_MODELS: dict[AcademicCatalogType, type] = {
    AcademicCatalogType.major: Major,
    AcademicCatalogType.minor: Minor,
    AcademicCatalogType.academic_interest: AcademicInterest,
    AcademicCatalogType.university: University,
    AcademicCatalogType.country: Country,
}

INTEGER_ID_TYPES = {
    AcademicCatalogType.major,
    AcademicCatalogType.minor,
    AcademicCatalogType.academic_interest,
}

_NOT_FOUND_MESSAGES = {
    AcademicCatalogType.major: "Major not found",
    AcademicCatalogType.minor: "Minor not found",
    AcademicCatalogType.academic_interest: "Academic interest not found",
    AcademicCatalogType.university: "University not found",
    AcademicCatalogType.country: "Country not found",
}


def catalog_model(catalog_type: AcademicCatalogType):
    return CATALOG_MODELS[catalog_type]


def not_found_message(catalog_type: AcademicCatalogType) -> str:
    return _NOT_FOUND_MESSAGES[catalog_type]


def parse_catalog_id(catalog_type: AcademicCatalogType, record_id: str) -> int | UUID | None:
    value = (record_id or "").strip()
    if not value:
        return None
    if catalog_type in INTEGER_ID_TYPES:
        if not value.isdigit():
            return None
        parsed = int(value)
        return parsed if parsed > 0 else None
    try:
        return UUID(value)
    except ValueError:
        return None


async def get_catalog_record(
    db: AsyncSession,
    catalog_type: AcademicCatalogType,
    record_id: Any,
):
    model = catalog_model(catalog_type)
    return (
        await db.execute(select(model).where(model.id == record_id))
    ).scalar_one_or_none()
