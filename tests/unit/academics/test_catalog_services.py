from __future__ import annotations

from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

from apps.academics.schemas import (
    AcademicInterestAdminItem,
    CatalogNameItem,
    CountryAdminItem,
    UniversityAdminItem,
)
from apps.academics.services import (
    build_bulk_create_message,
    bulk_create_academic_interests,
    bulk_create_countries,
    bulk_create_majors,
    bulk_create_minors,
    bulk_create_universities,
    list_test_interests,
    list_test_majors,
    list_test_minors,
)
from apps.profiles.db_models.academic_interests_db_model import AcademicInterest
from apps.profiles.db_models.country_db_model import Country
from apps.profiles.db_models.education_level_db_model import EducationLevel
from apps.profiles.db_models.major_db_model import Major
from apps.profiles.db_models.minor_db_model import Minor
from apps.profiles.db_models.profile_db_model import Profile
from apps.profiles.db_models.university_db_model import University


async def _prepare_sqlite():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    tables = [
        Major.__table__,
        Minor.__table__,
        EducationLevel.__table__,
        AcademicInterest.__table__,
        Country.__table__,
        University.__table__,
    ]

    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: SQLModel.metadata.create_all(sync_conn, tables=tables)
        )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


@pytest_asyncio.fixture
async def db_session():
    engine, factory = await _prepare_sqlite()
    async with factory() as session:
        yield session
    await engine.dispose()


def test_academic_interest_schema_allows_blank_major_for_soft_fail() -> None:
    item = AcademicInterestAdminItem.model_validate(
        {"name": "Deep Learning", "major": None, "minor": 10}
    )
    assert item.major is None
    assert item.minor == 10

    omitted = AcademicInterestAdminItem.model_validate({"name": "Optics"})
    assert omitted.major is None

    blank = AcademicInterestAdminItem.model_validate({"name": "Optics", "major": "  "})
    assert blank.major is None


def test_academic_interest_schema_accepts_major_minor_names() -> None:
    item = AcademicInterestAdminItem.model_validate(
        {"name": "Aaaaaaaa", "major": "batista", "minor": "undertaker"}
    )
    assert item.major == "batista"
    assert item.minor == "undertaker"


def _assert_bulk_invariant(result: dict) -> None:
    assert result["total"] == result["created"] + result["existing"] + result["failed"]
    assert len(result["items"]) == result["created"]
    assert len(result["already_existing"]) == result["existing"]
    assert len(result["failures"]) == result["failed"]
    assert "already_existing" in result
    assert "failures" in result


def test_build_bulk_create_message_mixed_counts() -> None:
    assert build_bulk_create_message(
        entity="interest", created=3, existing=4, failed=2
    ) == (
        "3 interests added successfully, 2 interests failed to create, "
        "4 interests already exists"
    )
    assert (
        build_bulk_create_message(entity="major", created=2, existing=0, failed=0)
        == "2 majors added successfully"
    )
    assert (
        build_bulk_create_message(entity="country", created=0, existing=1, failed=0)
        == "1 countries already exists"
    )


@pytest.mark.asyncio
async def test_bulk_create_single_and_multiple_majors(db_session) -> None:
    first = await bulk_create_majors(
        [CatalogNameItem(name="Accounting")],
        db_session,
    )
    _assert_bulk_invariant(first)
    assert first["created"] == 1
    assert first["existing"] == 0
    assert first["failed"] == 0
    assert first["already_existing"] == []
    assert first["failures"] == []
    assert first["items"][0]["name"] == "Accounting"
    assert first["items"][0]["isActive"] is True
    assert first["items"][0]["createdAt"] is not None
    assert first["items"][0]["updatedAt"] is not None

    second = await bulk_create_majors(
        [
            CatalogNameItem(name="Accounting"),
            CatalogNameItem(name="computer science"),
            CatalogNameItem(name="Artificial Intelligence", is_active=False),
        ],
        db_session,
    )
    _assert_bulk_invariant(second)
    assert second["created"] == 2
    assert second["existing"] == 1
    assert second["failed"] == 0
    created_names = {item["name"] for item in second["items"]}
    assert created_names == {"Computer Science", "Artificial Intelligence"}
    assert "Accounting" not in created_names
    assert second["already_existing"][0]["name"] == "Accounting"
    assert all(item.get("name") != "Accounting" for item in second["items"])


@pytest.mark.asyncio
async def test_bulk_create_majors_duplicate_in_request_and_inactive(db_session) -> None:
    result = await bulk_create_majors(
        [
            CatalogNameItem(name="Biology"),
            CatalogNameItem(name="biology"),
            CatalogNameItem(name="Chemistry", is_active=False),
        ],
        db_session,
    )
    _assert_bulk_invariant(result)
    assert result["created"] == 2
    assert result["existing"] >= 1
    assert result["failures"] == []
    chemistry = next(item for item in result["items"] if item.get("name") == "Chemistry")
    assert chemistry["isActive"] is False
    assert any(item.get("duplicateInRequest") for item in result["already_existing"])
    assert all(not item.get("duplicateInRequest") for item in result["items"])


@pytest.mark.asyncio
async def test_bulk_create_majors_soft_fails_invalid_names(db_session) -> None:
    result = await bulk_create_majors(
        [
            {"name": "Computer Science"},
            {"name": "1234"},
            {"name": "@#$%&*"},
            {"name": "Web3"},
            {"name": "AI@"},
        ],
        db_session,
    )
    _assert_bulk_invariant(result)
    assert result["created"] == 2
    assert result["failed"] == 3
    assert {item["name"] for item in result["items"]} == {"Computer Science", "Web3"}
    failures_by_name = {item["name"]: item["reason"] for item in result["failures"]}
    assert "1234" in failures_by_name
    assert "@#$%&*" in failures_by_name
    assert "AI@" in failures_by_name
    assert "at least one letter" in failures_by_name["1234"]
    assert "may only contain" in failures_by_name["AI@"]


@pytest.mark.asyncio
async def test_bulk_create_minors_soft_fails_invalid_names(db_session) -> None:
    result = await bulk_create_minors(
        [
            {"name": "Cybersecurity"},
            {"name": "42"},
            {"name": "Machine learning & Data science"},
        ],
        db_session,
    )
    _assert_bulk_invariant(result)
    assert result["created"] == 2
    assert result["failed"] == 1
    assert {item["name"] for item in result["items"]} == {
        "Cybersecurity",
        "Machine Learning & Data Science",
    }
    assert result["failures"][0]["name"] == "42"


@pytest.mark.asyncio
async def test_bulk_create_1500_majors(db_session) -> None:
    items = [CatalogNameItem(name=f"Major {index:04d}") for index in range(1500)]
    result = await bulk_create_majors(items, db_session)
    _assert_bulk_invariant(result)
    assert result["created"] == 1500
    assert result["existing"] == 0
    assert result["failed"] == 0
    assert result["already_existing"] == []
    assert result["failures"] == []
    replay = await bulk_create_majors(items[:10], db_session)
    _assert_bulk_invariant(replay)
    assert replay["created"] == 0
    assert replay["existing"] == 10
    assert replay["failed"] == 0
    assert replay["items"] == []
    assert len(replay["already_existing"]) == 10
    assert replay["failures"] == []


@pytest.mark.asyncio
async def test_bulk_create_minors_independent_of_major(db_session) -> None:
    result = await bulk_create_minors(
        [
            CatalogNameItem(name="Artificial Intelligence"),
            CatalogNameItem(name="Cybersecurity"),
            CatalogNameItem(name="Artificial Intelligence"),
        ],
        db_session,
    )
    _assert_bulk_invariant(result)
    _assert_bulk_invariant(result)
    assert result["created"] == 2
    assert result["existing"] == 1
    assert result["failed"] == 0
    assert {item["name"] for item in result["items"]} == {
        "Artificial Intelligence",
        "Cybersecurity",
    }
    assert len(result["already_existing"]) == 1
    assert result["already_existing"][0].get("duplicateInRequest") is True
    assert "major_id" not in Minor.model_fields


@pytest.mark.asyncio
async def test_bulk_create_majors_mixed_new_existing(db_session) -> None:
    await bulk_create_majors([CatalogNameItem(name="Existing Major")], db_session)
    result = await bulk_create_majors(
        [
            CatalogNameItem(name="Existing Major"),
            CatalogNameItem(name="Brand New Major"),
        ],
        db_session,
    )
    _assert_bulk_invariant(result)
    assert result["total"] == 2
    assert result["created"] == 1
    assert result["existing"] == 1
    assert result["failed"] == 0
    assert result["items"][0]["name"] == "Brand New Major"
    assert result["already_existing"][0]["name"] == "Existing Major"
    assert result["failures"] == []


@pytest.mark.asyncio
async def test_bulk_create_academic_interests_mixed_new_existing_failed(db_session) -> None:
    majors = await bulk_create_majors([CatalogNameItem(name="Computer Science")], db_session)
    major_id = int(majors["items"][0]["id"])
    await bulk_create_academic_interests(
        [AcademicInterestAdminItem(name="Existing Interest", major_id=major_id)],
        db_session,
    )
    result = await bulk_create_academic_interests(
        [
            AcademicInterestAdminItem(name="Existing Interest", major_id=major_id),
            AcademicInterestAdminItem(name="New Interest", major_id=major_id),
            AcademicInterestAdminItem(name="Broken Interest", major_id=999999),
        ],
        db_session,
    )
    _assert_bulk_invariant(result)
    assert result["total"] == 3
    assert result["created"] == 1
    assert result["existing"] == 1
    assert result["failed"] == 1
    assert result["items"][0]["name"] == "New Interest"
    assert result["already_existing"][0]["name"] == "Existing Interest"
    assert result["failures"][0] == {"name": "Broken Interest", "reason": "major not found"}
    assert all(item["name"] != "Existing Interest" for item in result["items"])
    assert all(item["name"] != "New Interest" for item in result["already_existing"])


@pytest.mark.asyncio
async def test_bulk_create_countries_all_existing_and_mixed(db_session) -> None:
    first = await bulk_create_countries(
        [
            CountryAdminItem(name="India", iso_code="IN"),
            CountryAdminItem(name="United States", iso_code="US"),
        ],
        db_session,
    )
    _assert_bulk_invariant(first)
    assert first["created"] == 2
    assert first["existing"] == 0
    assert first["failed"] == 0
    assert first["already_existing"] == []
    assert first["failures"] == []

    replay = await bulk_create_countries(
        [
            CountryAdminItem(name="Republic of India", iso_code="IN"),
            CountryAdminItem(name="USA", iso_code="US"),
            CountryAdminItem(name="Canada", iso_code="CA"),
        ],
        db_session,
    )
    _assert_bulk_invariant(replay)
    assert replay["total"] == 3
    assert replay["created"] == 1
    assert replay["existing"] == 2
    assert replay["failed"] == 0
    assert replay["items"][0]["isoCode"] == "CA"
    assert {item["isoCode"] for item in replay["already_existing"]} == {"IN", "US"}
    assert all(item["isoCode"] != "CA" for item in replay["already_existing"])
    assert all(item.get("isoCode") != "IN" for item in replay["items"])
    assert replay["failures"] == []


@pytest.mark.asyncio
async def test_bulk_create_countries_invalid_iso_format_soft_fails(db_session) -> None:
    result = await bulk_create_countries(
        [
            CountryAdminItem(name="India", iso_code="IN"),
            CountryAdminItem(name="United States of America", iso_code="USA"),
            CountryAdminItem(name="Numericland", iso_code="12"),
            CountryAdminItem(name="Canada", iso_code="CA"),
            {"name": "Blank Code", "iso_code": ""},
            {"iso_code": "ZZ"},
            {"name": "No Code"},
        ],
        db_session,
    )
    _assert_bulk_invariant(result)
    assert result["created"] == 2
    assert result["existing"] == 0
    assert result["failed"] == 5
    assert {item["isoCode"] for item in result["items"]} == {"IN", "CA"}
    assert result["already_existing"] == []
    failures_by_name = {item["name"]: item["reason"] for item in result["failures"]}
    assert failures_by_name["United States of America"] == "ISO code format does not match"
    assert failures_by_name["Numericland"] == "ISO code format does not match"
    assert failures_by_name["Blank Code"] == "iso_code cannot be blank"
    assert failures_by_name[""] == "name cannot be blank"
    assert failures_by_name["No Code"] == "iso_code cannot be blank"
    assert build_bulk_create_message(
        entity="country",
        created=result["created"],
        existing=result["existing"],
        failed=result["failed"],
    ) == "2 countries added successfully, 5 countries failed to create"


@pytest.mark.asyncio
async def test_academic_interest_major_only_and_major_minor(db_session) -> None:
    majors = await bulk_create_majors(
        [CatalogNameItem(name="Computer Science")],
        db_session,
    )
    minors = await bulk_create_minors(
        [CatalogNameItem(name="Artificial Intelligence")],
        db_session,
    )
    major_id = int(majors["items"][0]["id"])
    minor_id = int(minors["items"][0]["id"])

    result = await bulk_create_academic_interests(
        [
            AcademicInterestAdminItem(name="Algorithms", major_id=major_id, minor_id=None),
            AcademicInterestAdminItem(name="Machine Learning", major_id=major_id, minor_id=minor_id),
            AcademicInterestAdminItem(name="Deep Learning", major_id=major_id, minor_id=minor_id),
        ],
        db_session,
    )
    assert result["created"] == 3
    assert result["failed"] == 0

    rows = list((await db_session.execute(select(AcademicInterest))).scalars().all())
    by_name = {row.name: row for row in rows if row.major_id == major_id}
    assert by_name["Algorithms"].minor_id is None
    assert by_name["Algorithms"].education_level_id is None
    assert by_name["Machine Learning"].minor_id == minor_id
    assert by_name["Deep Learning"].education_level_id is None


@pytest.mark.asyncio
async def test_academic_interest_duplicate_combination_and_replay(db_session) -> None:
    majors = await bulk_create_majors([CatalogNameItem(name="Physics")], db_session)
    major_id = int(majors["items"][0]["id"])
    payload = [
        AcademicInterestAdminItem(name="Optics", major_id=major_id),
        AcademicInterestAdminItem(name="Optics", major_id=major_id),
    ]
    first = await bulk_create_academic_interests(payload, db_session)
    _assert_bulk_invariant(first)
    assert first["created"] == 1
    assert first["existing"] == 1
    assert first["failed"] == 0
    assert len(first["items"]) == 1
    assert first["items"][0]["name"] == "Optics"
    assert first["already_existing"][0].get("duplicateInRequest") is True
    replay = await bulk_create_academic_interests(
        [AcademicInterestAdminItem(name="Optics", major_id=major_id)],
        db_session,
    )
    _assert_bulk_invariant(replay)
    assert replay["created"] == 0
    assert replay["existing"] == 1
    assert replay["items"] == []
    original_id = first["items"][0]["id"]
    replay_id = replay["already_existing"][0]["id"]
    assert replay_id == original_id


@pytest.mark.asyncio
async def test_academic_interest_rejects_blank_major(db_session) -> None:
    majors = await bulk_create_majors([CatalogNameItem(name="Computer Science")], db_session)
    major_id = int(majors["items"][0]["id"])
    result = await bulk_create_academic_interests(
        [
            AcademicInterestAdminItem.model_validate(
                {"name": "Algorithms", "major": major_id}
            ),
            AcademicInterestAdminItem.model_validate(
                {"name": "Deep Learning", "major": None, "minor": 10}
            ),
            AcademicInterestAdminItem.model_validate({"name": "Optics"}),
            {"name": "Zero Major", "major": 0},
            {"name": "Missing Name Major", "major": major_id},
            {"major": major_id},
        ],
        db_session,
    )
    _assert_bulk_invariant(result)
    assert result["created"] == 2
    assert result["existing"] == 0
    assert result["failed"] == 4
    created_names = {item["name"] for item in result["items"]}
    assert created_names == {"Algorithms", "Missing Name Major"}
    reasons = {item["reason"] for item in result["failures"]}
    assert "major cannot be blank" in reasons
    assert "major id must be a positive integer" in reasons
    assert "name cannot be blank" in reasons


@pytest.mark.asyncio
async def test_academic_interest_rejects_unknown_major(db_session) -> None:
    result = await bulk_create_academic_interests(
        [AcademicInterestAdminItem(name="Algorithms", major_id=999999)],
        db_session,
    )
    _assert_bulk_invariant(result)
    assert result["created"] == 0
    assert result["existing"] == 0
    assert result["failed"] == 1
    assert result["items"] == []
    assert result["already_existing"] == []
    assert result["failures"][0]["reason"] == "major not found"
    assert result["failures"][0]["name"] == "Algorithms"
    assert list((await db_session.execute(select(AcademicInterest))).scalars().all()) == []


@pytest.mark.asyncio
async def test_academic_interest_rejects_unknown_major_and_minor_names(db_session) -> None:
    result = await bulk_create_academic_interests(
        [
            AcademicInterestAdminItem.model_validate(
                {"name": "Aaaaaaaa", "major": "batista", "minor": "undertaker"}
            ),
            AcademicInterestAdminItem.model_validate(
                {"name": "Bbbbbb", "major": "BATISTA"}
            ),
        ],
        db_session,
    )
    _assert_bulk_invariant(result)
    assert result["created"] == 0
    assert result["existing"] == 0
    assert result["failed"] == 2
    assert result["items"] == []
    assert result["already_existing"] == []
    assert {item["reason"] for item in result["failures"]} == {"major not found"}
    assert list((await db_session.execute(select(Major))).scalars().all()) == []
    assert list((await db_session.execute(select(Minor))).scalars().all()) == []
    assert list((await db_session.execute(select(AcademicInterest))).scalars().all()) == []


@pytest.mark.asyncio
async def test_academic_interest_rejects_unknown_minor_when_major_exists(db_session) -> None:
    majors = await bulk_create_majors([CatalogNameItem(name="Computer Science")], db_session)
    major_id = int(majors["items"][0]["id"])
    result = await bulk_create_academic_interests(
        [
            AcademicInterestAdminItem.model_validate(
                {"name": "Deep Learning", "major": major_id, "minor": 999999}
            )
        ],
        db_session,
    )
    _assert_bulk_invariant(result)
    assert result["created"] == 0
    assert result["existing"] == 0
    assert result["failed"] == 1
    assert result["failures"][0]["reason"] == "minor not found"
    assert list((await db_session.execute(select(AcademicInterest))).scalars().all()) == []


@pytest.mark.asyncio
async def test_academic_interest_resolves_existing_major_minor_from_names(db_session) -> None:
    await bulk_create_majors([CatalogNameItem(name="batista")], db_session)
    await bulk_create_minors([CatalogNameItem(name="undertaker")], db_session)

    result = await bulk_create_academic_interests(
        [
            AcademicInterestAdminItem.model_validate(
                {"name": "Aaaaaaaa", "major": "batista", "minor": "undertaker"}
            ),
            AcademicInterestAdminItem.model_validate(
                {"name": "Bbbbbb", "major": "BATISTA", "minor": "Undertaker"}
            ),
        ],
        db_session,
    )
    assert result["created"] == 2
    assert result["failed"] == 0

    majors = list((await db_session.execute(select(Major))).scalars().all())
    minors = list((await db_session.execute(select(Minor))).scalars().all())
    assert {row.name.lower() for row in majors} == {"batista"}
    assert {row.name.lower() for row in minors} == {"undertaker"}
    interests = list((await db_session.execute(select(AcademicInterest))).scalars().all())
    assert len({row.major_id for row in interests}) == 1
    assert len({row.minor_id for row in interests}) == 1


@pytest.mark.asyncio
async def test_interest_lookup_major_only_and_major_minor(db_session) -> None:
    majors = await bulk_create_majors(
        [
            CatalogNameItem(name="Computer Science"),
            CatalogNameItem(name="Mechanical Engineering"),
        ],
        db_session,
    )
    minors = await bulk_create_minors(
        [CatalogNameItem(name="Artificial Intelligence")],
        db_session,
    )
    cs_id = int(next(item["id"] for item in majors["items"] if item["name"] == "Computer Science"))
    mech_id = int(
        next(item["id"] for item in majors["items"] if item["name"] == "Mechanical Engineering")
    )
    ai_id = int(minors["items"][0]["id"])

    await bulk_create_academic_interests(
        [
            AcademicInterestAdminItem(name="Algorithms", major_id=cs_id),
            AcademicInterestAdminItem(name="Data Structures", major_id=cs_id),
            AcademicInterestAdminItem(name="Machine Learning", major_id=cs_id, minor_id=ai_id),
            AcademicInterestAdminItem(name="Deep Learning", major_id=cs_id, minor_id=ai_id),
            AcademicInterestAdminItem(name="NLP", major_id=cs_id, minor_id=ai_id),
            AcademicInterestAdminItem(name="Thermodynamics", major_id=mech_id),
        ],
        db_session,
    )

    major_only = await list_test_interests(
        major_id=cs_id, minor_id=None, query=None, page=None, page_size=None, db=db_session
    )
    assert {item["name"] for item in major_only["items"]} == {
        "Algorithms",
        "Data Structures",
        "Deep Learning",
        "Machine Learning",
        "NLP",
    }
    assert all(item["interest_added_by"] == "admin" for item in major_only["items"])
    assert all(item["majorName"] == "Computer Science" for item in major_only["items"])
    by_name_major = {item["name"]: item for item in major_only["items"]}
    assert by_name_major["Algorithms"]["minorName"] is None
    assert by_name_major["Machine Learning"]["minorName"] == "Artificial Intelligence"

    with_minor = await list_test_interests(
        major_id=cs_id, minor_id=ai_id, query=None, page=None, page_size=None, db=db_session
    )
    assert {item["name"] for item in with_minor["items"]} == {
        "Algorithms",
        "Data Structures",
        "Deep Learning",
        "Machine Learning",
        "NLP",
    }
    by_name = {item["name"]: item for item in with_minor["items"]}
    assert by_name["Machine Learning"]["majorName"] == "Computer Science"
    assert by_name["Machine Learning"]["minorName"] == "Artificial Intelligence"
    assert by_name["Algorithms"]["minorName"] is None

    # OR across catalogs: mech major interests union AI minor interests
    unrelated = await list_test_interests(
        major_id=mech_id, minor_id=ai_id, query=None, page=None, page_size=None, db=db_session
    )
    assert {item["name"] for item in unrelated["items"]} == {
        "Deep Learning",
        "Machine Learning",
        "NLP",
        "Thermodynamics",
    }
    by_name_or = {item["name"]: item for item in unrelated["items"]}
    assert by_name_or["Thermodynamics"]["majorName"] == "Mechanical Engineering"
    assert by_name_or["Machine Learning"]["minorName"] == "Artificial Intelligence"

    all_interests = await list_test_interests(
        major_id=None, minor_id=None, query=None, page=None, page_size=None, db=db_session
    )
    assert {item["name"] for item in all_interests["items"]} == {
        "Algorithms",
        "Data Structures",
        "Deep Learning",
        "Machine Learning",
        "NLP",
        "Thermodynamics",
    }

    minor_only = await list_test_interests(
        major_id=None, minor_id=ai_id, query=None, page=None, page_size=None, db=db_session
    )
    assert {item["name"] for item in minor_only["items"]} == {
        "Deep Learning",
        "Machine Learning",
        "NLP",
    }


@pytest.mark.asyncio
async def test_list_test_majors_and_minors_active_only(db_session) -> None:
    await bulk_create_majors(
        [
            CatalogNameItem(name="Active Major"),
            CatalogNameItem(name="Hidden Major", is_active=False),
        ],
        db_session,
    )
    await bulk_create_minors(
        [
            CatalogNameItem(name="Active Minor"),
            CatalogNameItem(name="Hidden Minor", is_active=False),
        ],
        db_session,
    )
    majors = await list_test_majors(query=None, page=1, page_size=20, db=db_session)
    minors = await list_test_minors(query=None, page=1, page_size=20, db=db_session)
    assert all(item["name"] != "Hidden Major" for item in majors["items"])
    assert all(item["name"] != "Hidden Minor" for item in minors["items"])
    assert any(item["name"] == "Active Major" for item in majors["items"])
    assert "id" in majors["items"][0]
    assert majors["items"][0]["major_added_by"] == "admin"
    assert minors["items"][0]["minor_added_by"] == "admin"


@pytest.mark.asyncio
async def test_list_test_majors_paginates_only_when_page_and_page_size_provided(
    db_session,
) -> None:
    await bulk_create_majors(
        [CatalogNameItem(name=f"Catalog Major {index:02d}") for index in range(5)],
        db_session,
    )
    all_rows = await list_test_majors(query="Catalog Major", page=None, page_size=None, db=db_session)
    assert all_rows["totalItems"] == 5
    assert len(all_rows["items"]) == 5
    assert all_rows["page"] == 1
    assert all_rows["pageSize"] == 5

    page_one = await list_test_majors(query="Catalog Major", page=1, page_size=2, db=db_session)
    assert page_one["totalItems"] == 5
    assert len(page_one["items"]) == 2
    assert page_one["page"] == 1
    assert page_one["pageSize"] == 2
    assert page_one["totalPages"] == 3


def test_profile_catalog_ids_remain_nullable() -> None:
    assert "major" in Profile.model_fields
    assert "minor" in Profile.model_fields
    assert "major_id" in Profile.model_fields
    assert "minor_id" in Profile.model_fields
    profile = Profile.model_construct(
        first_name="Existing",
        last_name="User",
        major="Computer Science",
        minor="Mathematics",
        completeness_score=0,
    )
    assert profile.major == "Computer Science"
    assert profile.minor == "Mathematics"
    assert profile.major_id is None
    assert profile.minor_id is None


@pytest.mark.asyncio
async def test_existing_education_level_interest_untouched(db_session) -> None:
    level = (
        await db_session.execute(select(EducationLevel).where(EducationLevel.id == 1))
    ).scalar_one_or_none()
    if level is None:
        db_session.add(EducationLevel(id=1, name="Bachelors", is_active=True))
        await db_session.flush()

    legacy = AcademicInterest(
        name=f"Legacy Interest {uuid4().hex[:6]}",
        education_level_id=1,
        is_active=True,
    )
    db_session.add(legacy)
    await db_session.commit()
    await db_session.refresh(legacy)
    assert legacy.education_level_id == 1
    assert legacy.major_id is None
    assert legacy.minor_id is None
    assert legacy.id is not None


@pytest.mark.asyncio
async def test_bulk_create_country_and_university(db_session) -> None:
    countries = await bulk_create_countries(
        [
            CountryAdminItem(name=f"Catalogland {''.join(c for c in uuid4().hex if c.isalpha())[:6]}", iso_code="QZ"),
            CountryAdminItem(
                name=f"Catalog States {''.join(c for c in uuid4().hex if c.isalpha())[:6]}",
                iso_code="QY",
                is_active=False,
            ),
        ],
        db_session,
    )
    created_or_existing = countries["created"] + countries["existing"]
    assert created_or_existing >= 1
    assert countries["failed"] == 0
    _assert_bulk_invariant(countries)
    india = next(item for item in countries["items"] if item.get("isoCode") in {"QZ", "QY"})
    country_id = UUID(str(india["id"]))

    universities = await bulk_create_universities(
        [
            UniversityAdminItem(
                name="Example University",
                slug=f"example-university-{uuid4().hex[:6]}",
                country_id=country_id,
                major=["Computer Science", "Data Science"],
                minor=["Artificial Intelligence"],
                academic_program=["B.Tech", "M.Tech"],
                website="https://example.edu",
            )
        ],
        db_session,
    )
    _assert_bulk_invariant(universities)
    assert universities["created"] == 1
    assert universities["already_existing"] == []
    assert universities["failures"] == []
    created = universities["items"][0]
    assert created["isActive"] is True
    assert any(item.get("name") == "Computer Science" for item in created["major"])

    catalog_majors = list((await db_session.execute(select(Major))).scalars().all())
    catalog_minors = list((await db_session.execute(select(Minor))).scalars().all())
    assert {row.name for row in catalog_majors} >= {"Computer Science", "Data Science"}
    assert {row.name for row in catalog_minors} >= {"Artificial Intelligence"}


@pytest.mark.asyncio
async def test_bulk_create_university_fails_when_country_is_missing(db_session) -> None:
    missing_id = uuid4()
    result = await bulk_create_universities(
        [
            UniversityAdminItem.model_validate(
                {
                    "name": "Missing Country University",
                    "slug": f"missing-country-{uuid4().hex[:6]}",
                    "country": str(missing_id),
                    "major": ["Robotics"],
                    "website": "https://missing-country.edu",
                }
            ),
            UniversityAdminItem.model_validate(
                {
                    "name": "Named Country University",
                    "slug": f"named-country-{uuid4().hex[:6]}",
                    "country": "Catalogland",
                    "major": ["Robotics"],
                    "website": "https://named-country.edu",
                }
            ),
        ],
        db_session,
    )
    _assert_bulk_invariant(result)
    assert result["created"] == 0
    assert result["existing"] == 0
    assert result["failed"] == 2
    assert result["items"] == []
    assert result["already_existing"] == []
    assert {item["reason"] for item in result["failures"]} == {"country not found"}
    assert list((await db_session.execute(select(University))).scalars().all()) == []
    assert list((await db_session.execute(select(Country))).scalars().all()) == []


@pytest.mark.asyncio
async def test_bulk_create_university_resolves_existing_country_name(db_session) -> None:
    countries = await bulk_create_countries(
        [CountryAdminItem(name="Catalogland", iso_code="QZ")],
        db_session,
    )
    country_id = countries["items"][0]["id"]

    first = await bulk_create_universities(
        [
            UniversityAdminItem.model_validate(
                {
                    "name": "Named Country University",
                    "slug": f"named-country-{uuid4().hex[:6]}",
                    "country": "Catalogland",
                    "major": ["Robotics"],
                    "minor": ["AI"],
                    "website": "https://named.edu",
                }
            )
        ],
        db_session,
    )
    _assert_bulk_invariant(first)
    assert first["created"] == 1
    assert first["existing"] == 0
    assert first["failed"] == 0
    assert first["already_existing"] == []
    assert first["items"][0]["countryId"] == country_id

    replay = await bulk_create_universities(
        [
            UniversityAdminItem.model_validate(
                {
                    "name": "Second Named University",
                    "slug": f"second-named-{uuid4().hex[:6]}",
                    "country": "catalogland",
                    "major": ["robotics"],
                    "website": "https://second-named.edu",
                }
            )
        ],
        db_session,
    )
    _assert_bulk_invariant(replay)
    assert replay["created"] == 1
    assert replay["items"][0]["countryId"] == country_id
    countries_after = list((await db_session.execute(select(Country))).scalars().all())
    assert len(countries_after) == 1
    majors = list((await db_session.execute(select(Major))).scalars().all())
    assert {row.name.lower() for row in majors} == {"robotics"}


@pytest.mark.asyncio
async def test_bulk_create_university_creates_valid_and_fails_missing_country(db_session) -> None:
    countries = await bulk_create_countries(
        [CountryAdminItem(name="Catalogland", iso_code="QZ")],
        db_session,
    )
    country_id = UUID(str(countries["items"][0]["id"]))
    result = await bulk_create_universities(
        [
            UniversityAdminItem(
                name="Valid University",
                slug=f"valid-university-{uuid4().hex[:6]}",
                country_id=country_id,
                website="https://valid.edu",
            ),
            UniversityAdminItem.model_validate(
                {
                    "name": "Invalid University",
                    "slug": f"invalid-university-{uuid4().hex[:6]}",
                    "country": str(uuid4()),
                    "website": "https://invalid.edu",
                }
            ),
        ],
        db_session,
    )
    _assert_bulk_invariant(result)
    assert result["created"] == 1
    assert result["existing"] == 0
    assert result["failed"] == 1
    assert result["items"][0]["name"] == "Valid University"
    assert result["already_existing"] == []
    assert result["failures"][0] == {"name": "Invalid University", "reason": "country not found"}
    rows = list((await db_session.execute(select(University))).scalars().all())
    assert len(rows) == 1
    assert rows[0].name == "Valid University"


@pytest.mark.asyncio
async def test_bulk_create_university_soft_fails_missing_required_fields(db_session) -> None:
    countries = await bulk_create_countries(
        [CountryAdminItem(name="Catalogland", iso_code="QZ")],
        db_session,
    )
    country_id = str(countries["items"][0]["id"])
    result = await bulk_create_universities(
        [
            {
                "name": "Complete University",
                "slug": f"complete-{uuid4().hex[:6]}",
                "country": country_id,
                "website": "https://complete.edu",
            },
            {
                "slug": f"no-name-{uuid4().hex[:6]}",
                "country": country_id,
                "website": "https://noname.edu",
            },
            {
                "name": "No Country University",
                "slug": f"no-country-{uuid4().hex[:6]}",
                "website": "https://nocountry.edu",
            },
            {
                "name": "No Website University",
                "slug": f"no-website-{uuid4().hex[:6]}",
                "country": country_id,
            },
            {
                "name": "Blank Website University",
                "slug": f"blank-website-{uuid4().hex[:6]}",
                "country": country_id,
                "website": "  ",
            },
        ],
        db_session,
    )
    _assert_bulk_invariant(result)
    assert result["created"] == 1
    assert result["failed"] == 4
    assert result["items"][0]["name"] == "Complete University"
    failures_by_name = {item["name"]: item["reason"] for item in result["failures"]}
    assert failures_by_name[""] == "name cannot be blank"
    assert failures_by_name["No Country University"] == "country cannot be blank"
    assert failures_by_name["No Website University"] == "website cannot be blank"
    assert failures_by_name["Blank Website University"] == "website cannot be blank"


@pytest.mark.asyncio
async def test_list_test_catalog_sorting_created_at(db_session) -> None:
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    m1 = Major(name="Alpha Major", created_at=now - timedelta(days=2))
    m2 = Major(name="Beta Major", created_at=now - timedelta(days=1))
    m3 = Major(name="Gamma Major", created_at=now)
    db_session.add_all([m1, m2, m3])
    await db_session.commit()

    # Sort by created_at with default order (should default to DESC / newest first)
    default_res = await list_test_majors(
        query=None,
        page=1,
        page_size=10,
        sort="created_at",
        db=db_session,
    )
    assert [item["name"] for item in default_res["items"]] == ["Gamma Major", "Beta Major", "Alpha Major"]

    # Sort by created_at DESC (newest first)
    desc_res = await list_test_majors(
        query=None,
        page=1,
        page_size=10,
        sort="created_at",
        order="desc",
        db=db_session,
    )
    assert [item["name"] for item in desc_res["items"]] == ["Gamma Major", "Beta Major", "Alpha Major"]

    # Sort by created_at ASC (oldest first)
    asc_res = await list_test_majors(
        query=None,
        page=1,
        page_size=10,
        sort="created_at",
        order="asc",
        db=db_session,
    )
    assert [item["name"] for item in asc_res["items"]] == ["Alpha Major", "Beta Major", "Gamma Major"]



