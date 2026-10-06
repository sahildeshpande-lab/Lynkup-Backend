from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator

from apps.academics.name_validation import (
    validate_country_name,
    validate_major_minor_interest_name,
    validate_university_name,
)


ADMIN_CATALOG_BULK_MAX_ITEMS = 1000


class CatalogBulkOperationType(str, Enum):
    """Optional ``?type=`` for admin catalog bulk POST endpoints.

    Omit for Add CTA (stricter ``status=false`` on duplicates/failures).
    Use ``import`` to keep the soft-fail bulk-import response semantics.
    """

    IMPORT = "import"


def _collapse_whitespace(value: str) -> str:
    return " ".join(value.split())


def normalize_catalog_id_or_name(value: object, *, field: str) -> int | str:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a string name or integer id")
    if isinstance(value, int):
        if value <= 0:
            raise ValueError(f"{field} id must be a positive integer")
        return value
    if isinstance(value, str):
        normalized = _collapse_whitespace(value)
        if not normalized:
            raise ValueError(f"{field} cannot be empty")
        return normalized
    raise ValueError(f"{field} must be a string name or integer id")


def normalize_optional_catalog_id_or_name(value: object, *, field: str) -> int | str | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = _collapse_whitespace(value)
        return normalized or None
    return normalize_catalog_id_or_name(value, field=field)


class CatalogNameItem(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    is_active: bool = True

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return validate_major_minor_interest_name(value)


class CatalogBulkRequest(BaseModel):
    """Item schema errors soft-fail in the service (bulk ``failures``)."""

    items: list[CatalogNameItem | dict[str, Any]] = Field(
        min_length=1, max_length=ADMIN_CATALOG_BULK_MAX_ITEMS
    )


class CatalogBulkProcessResult(BaseModel):
    """Shared bulk-processing result for admin catalog create/patch APIs."""

    total: int
    created: int
    existing: int
    failed: int
    items: list[dict[str, Any]] = Field(default_factory=list)
    already_existing: list[dict[str, Any]] = Field(default_factory=list)
    failures: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def enforce_count_invariant(self) -> "CatalogBulkProcessResult":
        if self.total != self.created + self.existing + self.failed:
            raise ValueError("total must equal created + existing + failed")
        return self


class CatalogBulkApiResponse(BaseModel):
    status: bool = True
    message: str = "success"
    data: CatalogBulkProcessResult | None = None


class AcademicInterestAdminItem(BaseModel):
    """Admin bulk-create academic interest row.

    Blank/missing ``major`` is allowed at parse time so the service can soft-fail
    the item into ``failures`` (``major cannot be blank``) instead of rejecting
    the whole request.
    """

    name: str = Field(min_length=1, max_length=100)
    major: int | str | None = Field(
        default=None,
        validation_alias=AliasChoices("major", "major_id", "majorId"),
    )
    minor: int | str | None = Field(
        default=None,
        validation_alias=AliasChoices("minor", "minor_id", "minorId"),
    )
    is_active: bool = True

    model_config = {"populate_by_name": True}

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return validate_major_minor_interest_name(value)

    @field_validator("major", mode="before")
    @classmethod
    def normalize_major(cls, value: object) -> int | str | None:
        return normalize_optional_catalog_id_or_name(value, field="major")

    @field_validator("minor", mode="before")
    @classmethod
    def normalize_minor(cls, value: object) -> int | str | None:
        return normalize_optional_catalog_id_or_name(value, field="minor")


class AcademicInterestBulkRequest(BaseModel):
    """Item schema errors soft-fail in the service (bulk ``failures``)."""

    items: list[AcademicInterestAdminItem | dict[str, Any]] = Field(
        min_length=1, max_length=ADMIN_CATALOG_BULK_MAX_ITEMS
    )


class UniversityAdminItem(BaseModel):
    """Admin bulk-create university row.

    Blank/missing ``name``, ``slug``, ``country``, and ``website`` soft-fail in
    the service into ``failures`` instead of rejecting the whole request.
    """

    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=255)
    country: UUID | str = Field(validation_alias=AliasChoices("country", "country_id", "countryId"))
    major: list[Any] | None = None
    minor: list[Any] | None = None
    academic_program: list[Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("academic_program", "academicProgram"),
    )
    is_active: bool = True
    website: str = Field(min_length=1, max_length=512)

    model_config = {"populate_by_name": True}

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return validate_university_name(value)

    @field_validator("slug")
    @classmethod
    def normalize_slug(cls, value: str) -> str:
        normalized = _collapse_whitespace(value)
        if not normalized:
            raise ValueError("value cannot be empty")
        return normalized

    @field_validator("country", mode="before")
    @classmethod
    def normalize_country(cls, value: object) -> UUID | str:
        if value is None:
            raise ValueError("country cannot be empty")
        if isinstance(value, UUID):
            return value
        if isinstance(value, str):
            normalized = _collapse_whitespace(value)
            if not normalized:
                raise ValueError("country cannot be empty")
            try:
                return UUID(normalized)
            except ValueError:
                return normalized
        raise ValueError("country must be a string name or UUID")

    @field_validator("website", mode="before")
    @classmethod
    def normalize_website(cls, value: object) -> str:
        if value is None:
            raise ValueError("website cannot be empty")
        if not isinstance(value, str):
            raise ValueError("website must be a string")
        normalized = value.strip()
        if not normalized:
            raise ValueError("website cannot be empty")
        return normalized


class UniversityBulkRequest(BaseModel):
    """Item schema errors soft-fail in the service (bulk ``failures``)."""

    items: list[UniversityAdminItem | dict[str, Any]] = Field(
        min_length=1, max_length=ADMIN_CATALOG_BULK_MAX_ITEMS
    )


class CountryAdminItem(BaseModel):
    """Bulk-create country row.

    Format checks for ``iso_code`` are enforced in the service so invalid
    values soft-fail into ``failures`` (``ISO code format does not match``)
    instead of rejecting the whole request with HTTP 422.
    """

    name: str = Field(min_length=1, max_length=128)
    iso_code: str = Field(
        min_length=1,
        max_length=16,
        validation_alias=AliasChoices("iso_code", "isoCode"),
    )
    is_active: bool = True

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return validate_country_name(value)

    @field_validator("iso_code")
    @classmethod
    def normalize_iso_code(cls, value: str) -> str:
        return value.strip().upper()


class CountryBulkRequest(BaseModel):
    """Item schema errors soft-fail in the service (bulk ``failures``)."""

    items: list[CountryAdminItem | dict[str, Any]] = Field(
        min_length=1, max_length=ADMIN_CATALOG_BULK_MAX_ITEMS
    )


class AcademicCatalogType(str, Enum):
    major = "major"
    minor = "minor"
    academic_interest = "academic_interest"
    university = "university"
    country = "country"


class AcademicCatalogSoftDeleteRequest(BaseModel):
    type: AcademicCatalogType
    id: str = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def normalize_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("id cannot be empty")
        return normalized


class UserAcademicInterestCreate(BaseModel):
    """Create interests under a major (and optional minor).

    ``major`` / ``minor`` accept either a catalog name (string) or catalog id (int).
    Numeric strings are treated as names, not ids.
    """

    major: int | str
    minor: int | str | None = None
    interests: list[str] = Field(min_length=1)

    @field_validator("major", mode="before")
    @classmethod
    def normalize_major(cls, value: object) -> int | str:
        return normalize_catalog_id_or_name(value, field="major")

    @field_validator("minor", mode="before")
    @classmethod
    def normalize_minor(cls, value: object) -> int | str | None:
        return normalize_optional_catalog_id_or_name(value, field="minor")

    @field_validator("interests")
    @classmethod
    def normalize_interests(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("interests cannot be empty")
        cleaned: list[str] = []
        for value in values:
            cleaned.append(validate_major_minor_interest_name(value, field="interest"))
        return cleaned


from apps.profiles.normalization import normalize_major_minor


class MajorPatchData(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    is_active: bool | None = Field(default=None, validation_alias=AliasChoices("is_active", "isActive"))

    model_config = {"extra": "forbid"}

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        validated = validate_major_minor_interest_name(value)
        normalized = normalize_major_minor(validated)
        if not normalized:
            raise ValueError("name cannot be empty")
        return normalized


class MinorPatchData(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    is_active: bool | None = Field(default=None, validation_alias=AliasChoices("is_active", "isActive"))

    model_config = {"extra": "forbid"}

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        validated = validate_major_minor_interest_name(value)
        normalized = normalize_major_minor(validated)
        if not normalized:
            raise ValueError("name cannot be empty")
        return normalized


class AcademicInterestPatchData(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    major_id: int | None = Field(default=None, validation_alias=AliasChoices("major_id", "majorId"))
    minor_id: int | None = Field(default=None, validation_alias=AliasChoices("minor_id", "minorId"))
    is_active: bool | None = Field(default=None, validation_alias=AliasChoices("is_active", "isActive"))

    model_config = {"extra": "forbid"}

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_major_minor_interest_name(value)


class UniversityPatchData(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(default=None, min_length=1, max_length=255)
    country_id: UUID | None = Field(default=None, validation_alias=AliasChoices("country_id", "countryId"))
    major: list[Any] | None = None
    minor: list[Any] | None = None
    academic_program: list[Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("academic_program", "academicProgram"),
    )
    is_active: bool | None = Field(default=None, validation_alias=AliasChoices("is_active", "isActive"))
    website: str | None = None

    model_config = {"extra": "forbid"}

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_university_name(value)

    @field_validator("slug")
    @classmethod
    def normalize_slug(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = _collapse_whitespace(value)
        if not normalized:
            raise ValueError("value cannot be empty")
        return normalized

    @field_validator("website")
    @classmethod
    def normalize_website(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class CountryPatchData(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    iso_code: str | None = Field(
        default=None,
        min_length=2,
        max_length=2,
        validation_alias=AliasChoices("iso_code", "isoCode"),
    )
    is_active: bool | None = Field(default=None, validation_alias=AliasChoices("is_active", "isActive"))

    model_config = {"extra": "forbid"}

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_country_name(value)

    @field_validator("iso_code")
    @classmethod
    def normalize_iso_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if len(normalized) != 2 or not normalized.isalpha():
            raise ValueError("iso_code must be a 2-letter code")
        return normalized


class AcademicCatalogPatchRequest(BaseModel):
    type: AcademicCatalogType
    id: str = Field(min_length=1)
    data: dict[str, Any]

    @field_validator("id")
    @classmethod
    def normalize_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("id cannot be empty")
        return normalized

    @model_validator(mode="after")
    def validate_data_payload(self) -> "AcademicCatalogPatchRequest":
        if not self.data:
            raise ValueError("At least one editable field must be provided in data")

        if self.type == AcademicCatalogType.major:
            parsed_data = MajorPatchData.model_validate(self.data)
        elif self.type == AcademicCatalogType.minor:
            parsed_data = MinorPatchData.model_validate(self.data)
        elif self.type == AcademicCatalogType.academic_interest:
            parsed_data = AcademicInterestPatchData.model_validate(self.data)
        elif self.type == AcademicCatalogType.university:
            parsed_data = UniversityPatchData.model_validate(self.data)
        elif self.type == AcademicCatalogType.country:
            parsed_data = CountryPatchData.model_validate(self.data)
        else:
            raise ValueError(f"Invalid catalog type: {self.type}")

        self.data = parsed_data.model_dump(exclude_unset=True, by_alias=False)
        return self
