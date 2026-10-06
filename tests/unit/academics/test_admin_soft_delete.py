from __future__ import annotations

from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from apps.academics.repositories import CATALOG_MODELS
from apps.academics.schemas import (
    AcademicCatalogSoftDeleteRequest,
    AcademicCatalogType,
    AcademicInterestAdminItem,
    CatalogNameItem,
    CountryAdminItem,
    UniversityAdminItem,
)
from apps.academics.services import (
    bulk_create_academic_interests,
    bulk_create_countries,
    bulk_create_majors,
    bulk_create_minors,
    bulk_create_universities,
    list_test_interests,
    list_test_majors,
    list_test_minors,
    soft_delete_catalog_record,
)
from apps.profiles.db_models.academic_interests_db_model import AcademicInterest
from apps.profiles.db_models.country_db_model import Country
from apps.profiles.db_models.education_level_db_model import EducationLevel
from apps.profiles.db_models.major_db_model import Major
from apps.profiles.db_models.minor_db_model import Minor
from apps.profiles.db_models.university_db_model import University
from common.exceptions import ApiError


def test_soft_delete_schema_rejects_invalid_type() -> None:
    with pytest.raises(ValidationError):
        AcademicCatalogSoftDeleteRequest.model_validate({"type": "degree", "id": "1"})


def test_catalog_model_allow_list_is_fixed() -> None:
    assert set(CATALOG_MODELS) == set(AcademicCatalogType)
    assert {model.__tablename__ for model in CATALOG_MODELS.values()} == {
        "majors",
        "minors",
        "academic_interests",
        "universities",
        "countries",
    }


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


async def _count(model, db) -> int:
    return int((await db.execute(select(func.count()).select_from(model))).scalar_one())


async def _seed_country_and_university(db_session):
    countries = await bulk_create_countries(
        [CountryAdminItem(name=f"Catalogland {''.join(c for c in uuid4().hex if c.isalpha())[:6]}", iso_code="QZ")],
        db_session,
    )
    country_id = UUID(str(countries["items"][0]["id"]))
    universities = await bulk_create_universities(
        [
            UniversityAdminItem(
                name="Example University",
                slug=f"example-university-{uuid4().hex[:6]}",
                country_id=country_id,
                website="https://example.edu",
            )
        ],
        db_session,
    )
    return str(country_id), str(universities["items"][0]["id"])


def _assert_deactivated(data: dict, catalog_type: AcademicCatalogType, record_id: str) -> None:
    assert data["type"] == catalog_type.value
    assert data["id"] == str(record_id)
    assert data["isActive"] is False
    assert data["updatedAt"] is not None


@pytest.mark.asyncio
async def test_soft_delete_major(db_session) -> None:
    created = await bulk_create_majors([CatalogNameItem(name="Accounting")], db_session)
    record_id = created["items"][0]["id"]
    previous_updated_at = created["items"][0]["updatedAt"]
    count_before = await _count(Major, db_session)

    data = await soft_delete_catalog_record(AcademicCatalogType.major, record_id, db_session)

    _assert_deactivated(data, AcademicCatalogType.major, record_id)
    row = (await db_session.execute(select(Major).where(Major.id == int(record_id)))).scalar_one()
    assert row.is_active is False
    assert row.id == int(record_id)
    assert row.updated_at is not None
    assert await _count(Major, db_session) == count_before
    listed = await list_test_majors(query=None, page=None, page_size=None, db=db_session)
    assert all(item["id"] != str(record_id) for item in listed["items"])
    if previous_updated_at:
        assert data["updatedAt"] >= previous_updated_at


@pytest.mark.asyncio
async def test_soft_delete_minor(db_session) -> None:
    created = await bulk_create_minors(
        [CatalogNameItem(name="Artificial Intelligence")], db_session
    )
    record_id = created["items"][0]["id"]
    count_before = await _count(Minor, db_session)

    data = await soft_delete_catalog_record(AcademicCatalogType.minor, record_id, db_session)

    _assert_deactivated(data, AcademicCatalogType.minor, record_id)
    row = (await db_session.execute(select(Minor).where(Minor.id == int(record_id)))).scalar_one()
    assert row.is_active is False
    assert await _count(Minor, db_session) == count_before
    listed = await list_test_minors(query=None, page=None, page_size=None, db=db_session)
    assert all(item["id"] != str(record_id) for item in listed["items"])


@pytest.mark.asyncio
async def test_soft_delete_academic_interest(db_session) -> None:
    majors = await bulk_create_majors([CatalogNameItem(name="Computer Science")], db_session)
    major_id = int(majors["items"][0]["id"])
    created = await bulk_create_academic_interests(
        [AcademicInterestAdminItem(name="Algorithms", major_id=major_id)],
        db_session,
    )
    record_id = created["items"][0]["id"]
    count_before = await _count(AcademicInterest, db_session)

    data = await soft_delete_catalog_record(
        AcademicCatalogType.academic_interest, record_id, db_session
    )

    _assert_deactivated(data, AcademicCatalogType.academic_interest, record_id)
    row = (
        await db_session.execute(
            select(AcademicInterest).where(AcademicInterest.id == int(record_id))
        )
    ).scalar_one()
    assert row.is_active is False
    assert row.major_id == major_id
    assert await _count(AcademicInterest, db_session) == count_before
    listed = await list_test_interests(
        major_id=major_id, minor_id=None, query=None, page=None, page_size=None, db=db_session
    )
    assert all(item["id"] != str(record_id) for item in listed["items"])


@pytest.mark.asyncio
async def test_soft_delete_university(db_session) -> None:
    country_id, university_id = await _seed_country_and_university(db_session)
    _ = country_id
    count_before = await _count(University, db_session)
    previous = (
        await db_session.execute(select(University).where(University.id == UUID(university_id)))
    ).scalar_one()
    previous_updated_at = previous.updated_at

    data = await soft_delete_catalog_record(
        AcademicCatalogType.university, university_id, db_session
    )

    _assert_deactivated(data, AcademicCatalogType.university, university_id)
    row = (
        await db_session.execute(select(University).where(University.id == UUID(university_id)))
    ).scalar_one()
    assert row.is_active is False
    assert row.updated_at >= previous_updated_at
    assert await _count(University, db_session) == count_before


@pytest.mark.asyncio
async def test_soft_delete_country(db_session) -> None:
    country_id, university_id = await _seed_country_and_university(db_session)
    count_before_countries = await _count(Country, db_session)
    count_before_universities = await _count(University, db_session)

    data = await soft_delete_catalog_record(AcademicCatalogType.country, country_id, db_session)

    _assert_deactivated(data, AcademicCatalogType.country, country_id)
    row = (
        await db_session.execute(select(Country).where(Country.id == UUID(country_id)))
    ).scalar_one()
    assert row.is_active is False
    assert await _count(Country, db_session) == count_before_countries
    university = (
        await db_session.execute(select(University).where(University.id == UUID(university_id)))
    ).scalar_one()
    assert university.is_active is True
    assert await _count(University, db_session) == count_before_universities


@pytest.mark.asyncio
async def test_soft_delete_invalid_id_is_not_found(db_session) -> None:
    with pytest.raises(ApiError, match="Major not found"):
        await soft_delete_catalog_record(AcademicCatalogType.major, "999999", db_session)
    with pytest.raises(ApiError, match="Major not found"):
        await soft_delete_catalog_record(AcademicCatalogType.major, "not-an-id", db_session)
    with pytest.raises(ApiError, match="University not found"):
        await soft_delete_catalog_record(
            AcademicCatalogType.university, str(uuid4()), db_session
        )
    with pytest.raises(ApiError, match="University not found"):
        await soft_delete_catalog_record(AcademicCatalogType.university, "not-a-uuid", db_session)


@pytest.mark.asyncio
async def test_soft_delete_already_inactive_is_idempotent(db_session) -> None:
    created = await bulk_create_majors(
        [CatalogNameItem(name="Hidden Major", is_active=False)],
        db_session,
    )
    record_id = created["items"][0]["id"]
    count_before = await _count(Major, db_session)

    first = await soft_delete_catalog_record(AcademicCatalogType.major, record_id, db_session)
    second = await soft_delete_catalog_record(AcademicCatalogType.major, record_id, db_session)

    assert first["isActive"] is False
    assert second["isActive"] is False
    assert second["id"] == record_id
    assert await _count(Major, db_session) == count_before
    row = (await db_session.execute(select(Major).where(Major.id == int(record_id)))).scalar_one()
    assert row.is_active is False


@pytest.mark.asyncio
async def test_soft_delete_major_does_not_cascade_or_modify_interests(db_session) -> None:
    majors = await bulk_create_majors([CatalogNameItem(name="Physics")], db_session)
    major_id = majors["items"][0]["id"]
    created = await bulk_create_academic_interests(
        [AcademicInterestAdminItem(name="Optics", major_id=int(major_id))],
        db_session,
    )
    interest_id = int(created["items"][0]["id"])
    interest_count = await _count(AcademicInterest, db_session)

    await soft_delete_catalog_record(AcademicCatalogType.major, major_id, db_session)

    interest = (
        await db_session.execute(
            select(AcademicInterest).where(AcademicInterest.id == interest_id)
        )
    ).scalar_one()
    assert interest.id == interest_id
    assert interest.major_id == int(major_id)
    assert interest.is_active is True
    assert interest.name == "Optics"
    assert await _count(AcademicInterest, db_session) == interest_count
    major = (
        await db_session.execute(select(Major).where(Major.id == int(major_id)))
    ).scalar_one()
    assert major.id == int(major_id)
    assert major.is_active is False
    assert major.updated_at is not None
