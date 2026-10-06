from __future__ import annotations

import pytest
from sqlmodel import select

from apps.academics.schemas import CatalogNameItem, CountryAdminItem
from apps.academics.services import bulk_create_countries, bulk_create_majors, bulk_create_minors
from apps.imports.enums import ImportType
from apps.moderation.db_models.moderation_words_db_model import ModerationWordsConfig
from apps.moderation.schemas import UpdateModerationWordsRequest
from apps.moderation.services.moderation_words_service import (
    get_moderation_words,
    update_moderation_words,
)
from apps.profiles.db_models.academic_interests_db_model import AcademicInterest
from apps.profiles.db_models.major_db_model import Major
from apps.profiles.db_models.minor_db_model import Minor
from apps.profiles.db_models.university_db_model import University
from common.exceptions import ApiError
from tests.unit.imports.conftest import assert_counts, csv_bytes, run_import

UNIVERSITY_HEADERS = ["name", "country", "major", "minor", "website"]


@pytest.mark.asyncio
async def test_country_successful_import(db_session) -> None:
    result = await run_import(
        db_session,
        ImportType.COUNTRY,
        "countries.csv",
        csv_bytes(
            ["name", "code"],
            [["India", "IN"], ["United States", "US"], ["Canada", "CA"]],
        ),
    )
    assert result["type"] == "country"
    assert_counts(result, successful=3, duplicate=0, failed=0)
    assert result["failedRows"] == []
    assert result["duplicateRows"] == []


@pytest.mark.asyncio
async def test_country_duplicate_in_file(db_session) -> None:
    result = await run_import(
        db_session,
        ImportType.COUNTRY,
        "countries.csv",
        csv_bytes(
            ["name", "code"],
            [["India", "IN"], ["Bharat", "in"]],
        ),
    )
    assert_counts(result, successful=1, duplicate=1, failed=0)
    assert result["duplicateRows"][0]["row"] == 3
    assert result["duplicateRows"][0]["reason"] == "Duplicate country in file"


@pytest.mark.asyncio
async def test_country_duplicate_already_in_db(db_session) -> None:
    await bulk_create_countries(
        [CountryAdminItem(name="India", iso_code="IN")],
        db_session,
    )
    result = await run_import(
        db_session,
        ImportType.COUNTRY,
        "countries.csv",
        csv_bytes(["name", "code"], [["India", "IN"], ["Nepal", "NP"]]),
    )
    assert_counts(result, successful=1, duplicate=1, failed=0)
    assert result["duplicateRows"][0]["reason"] == "Country already exists"
    assert result["duplicateRows"][0]["row"] == 2


@pytest.mark.asyncio
async def test_country_missing_required_column(db_session) -> None:
    with pytest.raises(ApiError, match="Missing required columns: code"):
        await run_import(
            db_session,
            ImportType.COUNTRY,
            "countries.csv",
            csv_bytes(["name"], [["India"]]),
        )


@pytest.mark.asyncio
async def test_country_invalid_data(db_session) -> None:
    result = await run_import(
        db_session,
        ImportType.COUNTRY,
        "countries.csv",
        csv_bytes(
            ["name", "code"],
            [["", "IN"], ["Mexico", "M1"]],
        ),
    )
    assert_counts(result, successful=0, duplicate=0, failed=2)
    reasons = {item["reason"] for item in result["failedRows"]}
    assert "Name is required" in reasons
    assert "Code must be a 2-letter code" in reasons


@pytest.mark.asyncio
async def test_university_successful_import(db_session) -> None:
    await bulk_create_countries(
        [CountryAdminItem(name="Catalogland", iso_code="QZ")],
        db_session,
    )
    result = await run_import(
        db_session,
        ImportType.UNIVERSITY,
        "universities.csv",
        csv_bytes(
            UNIVERSITY_HEADERS,
            [[
                "Example University",
                "Catalogland",
                "Computer Science, Data Science",
                "Artificial Intelligence",
                "https://example.edu",
            ]],
        ),
    )
    assert_counts(result, successful=1, duplicate=0, failed=0)
    row = (
        await db_session.execute(select(University).where(University.name == "Example University"))
    ).scalar_one()
    assert row.slug == "example-university"
    assert row.major == [{"name": "Computer Science"}, {"name": "Data Science"}]
    assert row.minor == [{"name": "Artificial Intelligence"}]
    assert row.academic_program is None


@pytest.mark.asyncio
async def test_university_invalid_and_missing_country(db_session) -> None:
    missing = await run_import(
        db_session,
        ImportType.UNIVERSITY,
        "universities.csv",
        csv_bytes(
            UNIVERSITY_HEADERS,
            [["Example University", "", "", "", ""]],
        ),
    )
    assert_counts(missing, successful=0, duplicate=0, failed=1)
    assert missing["failedRows"][0]["reason"] == "Country is required"

    unknown = await run_import(
        db_session,
        ImportType.UNIVERSITY,
        "universities.csv",
        csv_bytes(
            UNIVERSITY_HEADERS,
            [["Example University", "Narnia", "", "", ""]],
        ),
    )
    assert_counts(unknown, successful=0, duplicate=0, failed=1)
    assert unknown["failedRows"][0]["reason"] == "country not found"


@pytest.mark.asyncio
async def test_university_misspelled_country_uses_existing(db_session) -> None:
    created = await bulk_create_countries(
        [CountryAdminItem(name="Catalogland", iso_code="QZ")],
        db_session,
    )
    country_id = created["items"][0]["id"]
    result = await run_import(
        db_session,
        ImportType.UNIVERSITY,
        "universities.csv",
        csv_bytes(
            UNIVERSITY_HEADERS,
            [["Example University", "Cataloglnad", "", "", "https://example.edu"]],
        ),
    )
    assert_counts(result, successful=1, duplicate=0, failed=0)
    row = (
        await db_session.execute(select(University).where(University.name == "Example University"))
    ).scalar_one()
    assert str(row.country_id) == str(country_id)


@pytest.mark.asyncio
async def test_university_duplicate(db_session) -> None:
    await bulk_create_countries(
        [CountryAdminItem(name="Catalogland", iso_code="QY")],
        db_session,
    )
    first = await run_import(
        db_session,
        ImportType.UNIVERSITY,
        "universities.csv",
        csv_bytes(UNIVERSITY_HEADERS, [["Example University", "Catalogland", "", "", "https://example.edu"]]),
    )
    assert first["successfulCount"] == 1

    file_dup = await run_import(
        db_session,
        ImportType.UNIVERSITY,
        "universities.csv",
        csv_bytes(
            UNIVERSITY_HEADERS,
            [
                ["New Campus", "Catalogland", "", "", "https://new.edu"],
                ["New Campus", "Catalogland", "", "", "https://new.edu"],
            ],
        ),
    )
    assert_counts(file_dup, successful=1, duplicate=1, failed=0)
    assert file_dup["duplicateRows"][0]["reason"] == "Duplicate university in file"

    db_dup = await run_import(
        db_session,
        ImportType.UNIVERSITY,
        "universities.csv",
        csv_bytes(
            UNIVERSITY_HEADERS,
            [["Example University", "catalogland", "", "", "https://example.edu"]],
        ),
    )
    assert_counts(db_dup, successful=0, duplicate=1, failed=0)
    assert db_dup["duplicateRows"][0]["reason"] == "University already exists"


@pytest.mark.asyncio
async def test_major_successful_duplicate_and_missing_name(db_session) -> None:
    success = await run_import(
        db_session,
        ImportType.MAJOR,
        "majors.csv",
        csv_bytes(["name"], [["Accounting"], ["  Data Science "]]),
    )
    assert_counts(success, successful=2, duplicate=0, failed=0)

    mixed = await run_import(
        db_session,
        ImportType.MAJOR,
        "majors.csv",
        csv_bytes(
            ["name", "notes"],
            [
                ["Accounting", ""],
                ["Biology", ""],
                ["biology", ""],
                ["", "blank"],
            ],
        ),
    )
    assert_counts(mixed, successful=1, duplicate=2, failed=1)
    reasons = {item["reason"] for item in mixed["duplicateRows"]}
    assert "Major already exists" in reasons
    assert "Duplicate major in file" in reasons
    assert mixed["failedRows"][0]["reason"] == "Name is required"
    assert mixed["failedRows"][0]["row"] == 5


@pytest.mark.asyncio
async def test_major_missing_name_column(db_session) -> None:
    with pytest.raises(ApiError, match="Missing required columns: name"):
        await run_import(
            db_session,
            ImportType.MAJOR,
            "majors.csv",
            csv_bytes(["notes"], [["true"]]),
        )


@pytest.mark.asyncio
async def test_minor_successful_duplicate_missing_name_and_no_major_id(db_session) -> None:
    assert "major_id" not in Minor.model_fields
    success = await run_import(
        db_session,
        ImportType.MINOR,
        "minors.csv",
        csv_bytes(["name"], [["Artificial Intelligence"]]),
    )
    assert_counts(success, successful=1, duplicate=0, failed=0)

    mixed = await run_import(
        db_session,
        ImportType.MINOR,
        "minors.csv",
        csv_bytes(
            ["name", "notes"],
            [
                ["Artificial Intelligence", ""],
                ["Cybersecurity", ""],
                ["Cybersecurity", ""],
                ["", "blank"],
            ],
        ),
    )
    assert_counts(mixed, successful=1, duplicate=2, failed=1)
    assert any(item["reason"] == "Minor already exists" for item in mixed["duplicateRows"])
    assert any(item["reason"] == "Duplicate minor in file" for item in mixed["duplicateRows"])
    assert mixed["failedRows"][0]["reason"] == "Name is required"


@pytest.mark.asyncio
async def test_interest_successful_creates_missing_and_handles_misspellings(db_session) -> None:
    majors = await bulk_create_majors([CatalogNameItem(name="Computer Science")], db_session)
    minors = await bulk_create_minors([CatalogNameItem(name="Artificial Intelligence")], db_session)
    major_id = majors["items"][0]["id"]
    minor_id = minors["items"][0]["id"]

    success = await run_import(
        db_session,
        ImportType.INTEREST,
        "interests.csv",
        csv_bytes(
            ["name", "major"],
            [["Algorithms", "Computer Science"]],
        ),
    )
    assert_counts(success, successful=1, duplicate=0, failed=0)

    with_minor = await run_import(
        db_session,
        ImportType.INTEREST,
        "interests.csv",
        csv_bytes(
            ["name", "major", "minor"],
            [["Machine Learning", "Computre Science", "Artificial Intelligence"]],
        ),
    )
    assert_counts(with_minor, successful=1, duplicate=0, failed=0)
    created = (
        await db_session.execute(
            select(AcademicInterest).where(AcademicInterest.name == "Machine Learning")
        )
    ).scalar_one()
    assert str(created.major_id) == str(major_id)
    assert str(created.minor_id) == str(minor_id)

    created_major = await run_import(
        db_session,
        ImportType.INTEREST,
        "interests.csv",
        csv_bytes(
            ["name", "major", "minor"],
            [["Robotics", "Mechanical Engineering", ""]],
        ),
    )
    assert_counts(created_major, successful=1, duplicate=0, failed=0)
    new_major = (
        await db_session.execute(select(Major).where(Major.name == "Mechanical Engineering"))
    ).scalar_one()
    robotics = (
        await db_session.execute(select(AcademicInterest).where(AcademicInterest.name == "Robotics"))
    ).scalar_one()
    assert robotics.major_id == new_major.id
    assert robotics.minor_id is None

    invalid = await run_import(
        db_session,
        ImportType.INTEREST,
        "interests.csv",
        csv_bytes(
            ["name", "major", "minor"],
            [
                ["Optics", "Computer Science", ""],
                ["Algorithms", "Computer Science", ""],
                ["Machine Learning", "Computer Science", "Artificial Intelligence"],
                ["NLP", "Computer Science", "Artificial Intelligence"],
                ["NLP", "Computer Science", "Artificial Intelligence"],
                ["", "Computer Science", ""],
                ["Deep Learning", "", ""],
            ],
        ),
    )
    assert_counts(invalid, successful=2, duplicate=3, failed=2)
    reasons = {item["reason"] for item in invalid["failedRows"]}
    assert "Name is required" in reasons
    assert "Major is required" in reasons
    dup_reasons = {item["reason"] for item in invalid["duplicateRows"]}
    assert "Interest already exists" in dup_reasons
    assert "Duplicate interest in file" in dup_reasons


@pytest.mark.asyncio
async def test_interest_missing_required_columns(db_session) -> None:
    with pytest.raises(ApiError, match="Missing required columns: major"):
        await run_import(
            db_session,
            ImportType.INTEREST,
            "interests.csv",
            csv_bytes(["name"], [["Algorithms"]]),
        )


@pytest.mark.asyncio
async def test_interest_optional_minor_column_and_created_minor(db_session) -> None:
    result = await run_import(
        db_session,
        ImportType.INTEREST,
        "interests.csv",
        csv_bytes(
            ["name", "major", "minor"],
            [["Vision", "Biology", "Genetics"]],
        ),
    )
    assert_counts(result, successful=1, duplicate=0, failed=0)
    minor = (await db_session.execute(select(Minor).where(Minor.name == "Genetics"))).scalar_one()
    interest = (
        await db_session.execute(select(AcademicInterest).where(AcademicInterest.name == "Vision"))
    ).scalar_one()
    assert interest.minor_id == minor.id


@pytest.mark.asyncio
async def test_profanity_word_import_json_array(db_session) -> None:
    result = await run_import(
        db_session,
        ImportType.PROFANITY_WORD,
        "words.csv",
        csv_bytes(
            ["profanityWord", "notes"],
            [["BadWord", ""], ["Another", ""], ["badword", ""], ["", "blank"]],
        ),
    )
    assert_counts(result, successful=2, duplicate=1, failed=1)
    assert result["duplicateRows"][0]["reason"] == "Duplicate profanity word in file"
    assert result["failedRows"][0]["reason"] == "Profanity word is required"

    data = await get_moderation_words(db_session)
    assert data["profanityWords"] == ["another", "badword"]

    configs = list((await db_session.execute(select(ModerationWordsConfig))).scalars().all())
    assert len(configs) == 1
    assert configs[0].profanity_words == ["badword", "another"]

    existing = await run_import(
        db_session,
        ImportType.PROFANITY_WORD,
        "words.csv",
        csv_bytes(["profanityWord"], [["BadWord"], ["Click Here"]]),
    )
    assert_counts(existing, successful=1, duplicate=1, failed=0)
    assert existing["duplicateRows"][0]["reason"] == "Profanity word already exists"
    data = await get_moderation_words(db_session)
    assert "click here" in data["profanityWords"]
    assert "badword" in data["profanityWords"]


@pytest.mark.asyncio
async def test_profanity_import_does_not_replace_existing_list(db_session) -> None:
    await update_moderation_words(
        UpdateModerationWordsRequest(profanityWords=["keep-me"]),
        db_session,
    )
    result = await run_import(
        db_session,
        ImportType.PROFANITY_WORD,
        "words.csv",
        csv_bytes(["profanityWord"], [["new-word"]]),
    )
    assert result["successfulCount"] == 1
    data = await get_moderation_words(db_session)
    assert set(data["profanityWords"]) == {"new-word", "keep-me"}


@pytest.mark.asyncio
async def test_extra_columns_are_ignored(db_session) -> None:
    result = await run_import(
        db_session,
        ImportType.MAJOR,
        "majors.csv",
        csv_bytes(["name", "notes"], [["Physics", "ignore me"]]),
    )
    assert_counts(result, successful=1, duplicate=0, failed=0)


@pytest.mark.asyncio
async def test_partial_success_counts(db_session) -> None:
    result = await run_import(
        db_session,
        ImportType.MAJOR,
        "majors.csv",
        csv_bytes(
            ["name", "notes"],
            [
                ["Chemistry", ""],
                ["Chemistry", ""],
                ["", "blank"],
            ],
        ),
    )
    assert_counts(result, successful=1, duplicate=1, failed=1)
