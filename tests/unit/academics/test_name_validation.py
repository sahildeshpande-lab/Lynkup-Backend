from __future__ import annotations

import pytest
from pydantic import ValidationError

from apps.academics.name_validation import (
    validate_country_name,
    validate_major_minor_interest_name,
    validate_university_name,
)
from apps.academics.schemas import (
    CatalogNameItem,
    CountryAdminItem,
    UniversityAdminItem,
)


@pytest.mark.parametrize(
    "value",
    [
        "Computer Science",
        "Web3",
        "2D Art",
        "Machine learning & Data science",
        "Bio-Engineering",
    ],
)
def test_major_minor_interest_accepts_valid_names(value: str) -> None:
    assert validate_major_minor_interest_name(value) == " ".join(value.split())


@pytest.mark.parametrize(
    "value",
    ["1234", "@#$%&*", "!!!", "42", "&"],
)
def test_major_minor_interest_rejects_no_letters(value: str) -> None:
    with pytest.raises(ValueError, match="at least one letter"):
        validate_major_minor_interest_name(value)


@pytest.mark.parametrize(
    "value",
    ["AI@", "Data#Science", "CS!", "Hello_World"],
)
def test_major_minor_interest_rejects_disallowed_chars(value: str) -> None:
    with pytest.raises(ValueError, match="may only contain"):
        validate_major_minor_interest_name(value)


@pytest.mark.parametrize(
    "value",
    ["Guinea-Bissau", "United States", "Canada", "US"],
)
def test_country_accepts_valid_names(value: str) -> None:
    assert validate_country_name(value) == value


@pytest.mark.parametrize(
    "value",
    ["A", "C1", "France!", "United States 2", "St. Lucia"],
)
def test_country_rejects_invalid_names(value: str) -> None:
    with pytest.raises(ValueError):
        validate_country_name(value)


@pytest.mark.parametrize(
    "value",
    [
        "Texas A&M",
        "St. John's",
        "1 December University",
        "MIT",
    ],
)
def test_university_accepts_valid_names(value: str) -> None:
    assert validate_university_name(value) == value


@pytest.mark.parametrize(
    "value",
    ["1234", "!@#", "A", "42", "U@"],
)
def test_university_rejects_invalid_names(value: str) -> None:
    with pytest.raises(ValueError):
        validate_university_name(value)


def test_catalog_name_item_uses_major_rules() -> None:
    item = CatalogNameItem(name="Web3")
    assert item.name == "Web3"
    with pytest.raises(ValidationError):
        CatalogNameItem(name="1234")


def test_country_admin_item_uses_country_rules() -> None:
    item = CountryAdminItem(name="United States", iso_code="us")
    assert item.name == "United States"
    with pytest.raises(ValidationError):
        CountryAdminItem(name="USA1", iso_code="US")


def test_university_admin_item_uses_university_rules() -> None:
    item = UniversityAdminItem(
        name="Texas A&M",
        slug="texas-am",
        country="United States",
        website="https://example.edu",
    )
    assert item.name == "Texas A&M"
    with pytest.raises(ValidationError):
        UniversityAdminItem(
            name="1234",
            slug="bad",
            country="United States",
            website="https://example.edu",
        )
