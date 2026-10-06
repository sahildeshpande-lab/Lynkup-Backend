from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

import pytest
import pytest_asyncio
from pydantic import ValidationError

from apps.academics import services as academics_services
from apps.academics.schemas import CatalogNameItem, UserAcademicInterestCreate
from apps.academics.services import (
    bulk_create_majors,
    bulk_create_minors,
    create_user_academic_interests,
)
from apps.profiles.db_models.academic_interests_db_model import AcademicInterest
from apps.profiles.db_models.education_level_db_model import EducationLevel
from apps.profiles.db_models.major_db_model import Major
from apps.profiles.db_models.minor_db_model import Minor
from common.enums import InterestAddedBy
from common.exceptions import ApiError


async def _prepare_sqlite():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    tables = [
        Major.__table__,
        Minor.__table__,
        EducationLevel.__table__,
        AcademicInterest.__table__,
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


async def _seed_cs_and_ai(db_session):
    majors = await bulk_create_majors(
        [CatalogNameItem(name="Computer Science")],
        db_session,
    )
    minors = await bulk_create_minors(
        [CatalogNameItem(name="Artificial Intelligence")],
        db_session,
    )
    return int(majors["items"][0]["id"]), int(minors["items"][0]["id"])


def test_user_academic_interest_schema_requires_major_and_interests() -> None:
    with pytest.raises(ValidationError):
        UserAcademicInterestCreate.model_validate(
            {"minor": "AI", "interests": ["Algorithms"]}
        )
    with pytest.raises(ValidationError):
        UserAcademicInterestCreate.model_validate(
            {"major": "Computer Science", "interests": []}
        )
    with pytest.raises(ValidationError):
        UserAcademicInterestCreate.model_validate(
            {"major": "   ", "interests": ["Algorithms"]}
        )
    with pytest.raises(ValidationError):
        UserAcademicInterestCreate.model_validate(
            {"major": "Computer Science", "interests": ["   "]}
        )


def test_user_academic_interest_schema_allows_omitted_minor() -> None:
    payload = UserAcademicInterestCreate.model_validate(
        {"major": "  Computer   Science  ", "interests": ["  Algorithms  "]}
    )
    assert payload.major == "Computer Science"
    assert payload.minor is None
    assert payload.interests == ["Algorithms"]


def test_user_academic_interest_schema_accepts_integer_ids() -> None:
    payload = UserAcademicInterestCreate.model_validate(
        {"major": 12, "minor": 25, "interests": ["Machine Learning"]}
    )
    assert payload.major == 12
    assert payload.minor == 25
    assert payload.interests == ["Machine Learning"]


def test_user_academic_interest_schema_keeps_numeric_strings_as_names() -> None:
    payload = UserAcademicInterestCreate.model_validate(
        {"major": "12", "minor": "25", "interests": ["ML"]}
    )
    assert payload.major == "12"
    assert payload.minor == "25"


@pytest.mark.asyncio
async def test_major_name_plus_new_interest_succeeds(db_session) -> None:
    major_id, _minor_id = await _seed_cs_and_ai(db_session)
    result = await create_user_academic_interests(
        major="Computer Science",
        minor=None,
        interests=["Algorithms", "Data Structures"],
        db=db_session,
    )
    assert result["major"]["id"] == major_id
    assert result["major"]["name"] == "Computer Science"
    assert result["minor"] is None
    assert [item["name"] for item in result["items"]] == ["Algorithms", "Data Structures"]
    assert all(item["major_id"] == major_id for item in result["items"])
    assert all(item["minor_id"] is None for item in result["items"])
    assert all(item["interest_added_by"] == InterestAddedBy.user.value for item in result["items"])


@pytest.mark.asyncio
async def test_major_id_plus_new_interest_succeeds(db_session) -> None:
    major_id, _minor_id = await _seed_cs_and_ai(db_session)
    result = await create_user_academic_interests(
        major=major_id,
        minor=None,
        interests=["Test added"],
        db=db_session,
    )
    assert result["major"]["id"] == major_id
    assert [item["name"] for item in result["items"]] == ["Test added"]
    assert result["items"][0]["major_id"] == major_id
    assert result["items"][0]["minor_id"] is None


@pytest.mark.asyncio
async def test_same_major_same_interest_is_duplicate(db_session) -> None:
    await _seed_cs_and_ai(db_session)
    await create_user_academic_interests(
        major="Computer Science",
        minor=None,
        interests=["AI"],
        db=db_session,
    )
    with pytest.raises(ApiError, match="Duplicate academic interest combination"):
        await create_user_academic_interests(
            major="Computer Science",
            minor=None,
            interests=["AI"],
            db=db_session,
        )


@pytest.mark.asyncio
async def test_different_major_same_interest_succeeds(db_session) -> None:
    await bulk_create_majors(
        [
            CatalogNameItem(name="Computer Science"),
            CatalogNameItem(name="Data Science"),
        ],
        db_session,
    )
    first = await create_user_academic_interests(
        major="Computer Science",
        minor=None,
        interests=["AI"],
        db=db_session,
    )
    second = await create_user_academic_interests(
        major="Data Science",
        minor=None,
        interests=["AI"],
        db=db_session,
    )
    assert first["items"][0]["name"] == "AI"
    assert second["items"][0]["name"] == "AI"
    assert first["items"][0]["major_id"] != second["items"][0]["major_id"]
    rows = list((await db_session.execute(select(AcademicInterest))).scalars().all())
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_same_major_different_minor_same_interest_succeeds(db_session) -> None:
    major_id, _ = await _seed_cs_and_ai(db_session)
    minors = await bulk_create_minors(
        [
            CatalogNameItem(name="Machine Learning"),
            CatalogNameItem(name="NLP"),
        ],
        db_session,
    )
    ml_id = int(minors["items"][0]["id"])
    nlp_id = int(minors["items"][1]["id"])

    first = await create_user_academic_interests(
        major=major_id,
        minor=ml_id,
        interests=["AI"],
        db=db_session,
    )
    second = await create_user_academic_interests(
        major=major_id,
        minor=nlp_id,
        interests=["AI"],
        db=db_session,
    )
    assert first["items"][0]["minor_id"] == ml_id
    assert second["items"][0]["minor_id"] == nlp_id
    assert first["items"][0]["id"] != second["items"][0]["id"]


@pytest.mark.asyncio
async def test_same_major_same_minor_same_interest_is_duplicate(db_session) -> None:
    major_id, minor_id = await _seed_cs_and_ai(db_session)
    await create_user_academic_interests(
        major=major_id,
        minor=minor_id,
        interests=["Machine Learning"],
        db=db_session,
    )
    with pytest.raises(ApiError, match="Duplicate academic interest combination"):
        await create_user_academic_interests(
            major="Computer Science",
            minor="Artificial Intelligence",
            interests=["Machine Learning"],
            db=db_session,
        )


@pytest.mark.asyncio
async def test_minor_supplied_by_name_succeeds(db_session) -> None:
    major_id, minor_id = await _seed_cs_and_ai(db_session)
    result = await create_user_academic_interests(
        major="computer science",
        minor="artificial intelligence",
        interests=["Deep Learning"],
        db=db_session,
    )
    assert result["minor"]["id"] == minor_id
    assert result["minor"]["name"] == "Artificial Intelligence"
    assert result["items"][0]["major_id"] == major_id
    assert result["items"][0]["minor_id"] == minor_id


@pytest.mark.asyncio
async def test_minor_supplied_by_id_succeeds(db_session) -> None:
    major_id, minor_id = await _seed_cs_and_ai(db_session)
    result = await create_user_academic_interests(
        major=major_id,
        minor=minor_id,
        interests=["Deep Learning"],
        db=db_session,
    )
    assert result["minor"]["id"] == minor_id
    assert result["items"][0]["minor_id"] == minor_id


@pytest.mark.asyncio
async def test_minor_does_not_belong_to_major(db_session, monkeypatch) -> None:
    cs_id, ai_id = await _seed_cs_and_ai(db_session)
    await bulk_create_majors([CatalogNameItem(name="Biology")], db_session)
    biology = (
        await db_session.execute(select(Major).where(Major.name == "Biology"))
    ).scalar_one()

    real_resolve = academics_services._resolve_minor_ref

    async def resolve_owned_by_cs(minor, db):
        row = await real_resolve(minor, db)
        object.__setattr__(row, "major_id", cs_id)
        return row

    monkeypatch.setattr(academics_services, "_resolve_minor_ref", resolve_owned_by_cs)

    with pytest.raises(ApiError, match="Minor does not belong to the specified major"):
        await create_user_academic_interests(
            major=int(biology.id),
            minor=ai_id,
            interests=["Genomics"],
            db=db_session,
        )


@pytest.mark.asyncio
async def test_nonexistent_major_id_not_found(db_session) -> None:
    with pytest.raises(ApiError, match="Major not found"):
        await create_user_academic_interests(
            major=99999,
            minor=None,
            interests=["AI"],
            db=db_session,
        )


@pytest.mark.asyncio
async def test_nonexistent_major_name_creates_user_major(db_session) -> None:
    result = await create_user_academic_interests(
        major="Nero Aronotics",
        minor=None,
        interests=["Nero"],
        db=db_session,
    )
    assert result["major"]["name"] == "Nero Aronotics"
    assert result["major"]["major_added_by"] == "user"
    assert result["minor"] is None
    assert result["items"][0]["name"] == "Nero"
    assert result["items"][0]["major_id"] == result["major"]["id"]
    assert result["items"][0]["interest_added_by"] == "user"

    stored_major = (
        await db_session.execute(
            select(Major).where(Major.name == "Nero Aronotics")
        )
    ).scalar_one()
    assert stored_major.major_added_by == "user"
    assert stored_major.is_active is True


@pytest.mark.asyncio
async def test_nonexistent_minor_id_not_found(db_session) -> None:
    major_id, _ = await _seed_cs_and_ai(db_session)
    with pytest.raises(ApiError, match="Minor not found"):
        await create_user_academic_interests(
            major=major_id,
            minor=99999,
            interests=["AI"],
            db=db_session,
        )


@pytest.mark.asyncio
async def test_nonexistent_minor_name_creates_user_minor(db_session) -> None:
    await bulk_create_majors([CatalogNameItem(name="Computer Science")], db_session)
    result = await create_user_academic_interests(
        major="Computer Science",
        minor="Quantum Computing",
        interests=["Quantum Algorithms"],
        db=db_session,
    )
    assert result["major"]["name"] == "Computer Science"
    assert result["minor"]["name"] == "Quantum Computing"
    assert result["minor"]["minor_added_by"] == "user"
    assert result["items"][0]["name"] == "Quantum Algorithms"
    assert result["items"][0]["major_id"] == result["major"]["id"]
    assert result["items"][0]["minor_id"] == result["minor"]["id"]
    assert result["items"][0]["interest_added_by"] == "user"

    stored_minor = (
        await db_session.execute(
            select(Minor).where(Minor.name == "Quantum Computing")
        )
    ).scalar_one()
    assert stored_minor.minor_added_by == "user"
    assert stored_minor.is_active is True


@pytest.mark.asyncio
async def test_nonexistent_major_and_minor_both_created(db_session) -> None:
    result = await create_user_academic_interests(
        major="Bioinformatics",
        minor="Genomics",
        interests=["Sequence Alignment"],
        db=db_session,
    )
    assert result["major"]["name"] == "Bioinformatics"
    assert result["major"]["major_added_by"] == "user"
    assert result["minor"]["name"] == "Genomics"
    assert result["minor"]["minor_added_by"] == "user"
    assert result["items"][0]["name"] == "Sequence Alignment"
    assert result["items"][0]["major_id"] == result["major"]["id"]
    assert result["items"][0]["minor_id"] == result["minor"]["id"]


@pytest.mark.asyncio
async def test_case_insensitive_interest_duplicate(db_session) -> None:
    await _seed_cs_and_ai(db_session)
    await create_user_academic_interests(
        major="Computer Science",
        minor=None,
        interests=["Artificial Intelligence"],
        db=db_session,
    )
    with pytest.raises(ApiError, match="Duplicate academic interest combination"):
        await create_user_academic_interests(
            major="Computer Science",
            minor=None,
            interests=["artificial intelligence"],
            db=db_session,
        )
    with pytest.raises(ApiError, match="Duplicate academic interest combination"):
        await create_user_academic_interests(
            major="Computer Science",
            minor=None,
            interests=["ARTIFICIAL INTELLIGENCE"],
            db=db_session,
        )


@pytest.mark.asyncio
async def test_leading_trailing_spaces_do_not_create_duplicate(db_session) -> None:
    await _seed_cs_and_ai(db_session)
    payload = UserAcademicInterestCreate.model_validate(
        {"major": "Computer Science", "interests": ["  Algorithms  "]}
    )
    await create_user_academic_interests(
        major=payload.major,
        minor=payload.minor,
        interests=payload.interests,
        db=db_session,
    )
    replay = UserAcademicInterestCreate.model_validate(
        {"major": "Computer Science", "interests": ["Algorithms"]}
    )
    with pytest.raises(ApiError, match="Duplicate academic interest combination"):
        await create_user_academic_interests(
            major=replay.major,
            minor=replay.minor,
            interests=replay.interests,
            db=db_session,
        )


@pytest.mark.asyncio
async def test_request_dedupes_case_variants_before_insert(db_session) -> None:
    await _seed_cs_and_ai(db_session)
    result = await create_user_academic_interests(
        major="Computer Science",
        minor=None,
        interests=["Machine Learning", "machine learning", "MACHINE LEARNING"],
        db=db_session,
    )
    assert [item["name"] for item in result["items"]] == ["Machine Learning"]
    rows = list((await db_session.execute(select(AcademicInterest))).scalars().all())
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_existing_admin_interest_source_blocks_duplicate(db_session) -> None:
    major_id, minor_id = await _seed_cs_and_ai(db_session)
    admin_row = AcademicInterest(
        name="Machine Learning",
        major_id=major_id,
        minor_id=minor_id,
        education_level_id=None,
        is_active=True,
        interest_added_by=InterestAddedBy.admin.value,
    )
    db_session.add(admin_row)
    await db_session.commit()

    with pytest.raises(ApiError, match="Duplicate academic interest combination"):
        await create_user_academic_interests(
            major="Computer Science",
            minor="Artificial Intelligence",
            interests=["Machine Learning"],
            db=db_session,
        )


@pytest.mark.asyncio
async def test_existing_education_level_interest_does_not_block_major_scoped(
    db_session,
) -> None:
    db_session.add(EducationLevel(id=1, name="Bachelors", is_active=True))
    await db_session.flush()
    legacy = AcademicInterest(
        name="Machine Learning",
        education_level_id=1,
        is_active=True,
    )
    db_session.add(legacy)
    await db_session.commit()
    await db_session.refresh(legacy)
    legacy_id = legacy.id

    await _seed_cs_and_ai(db_session)
    result = await create_user_academic_interests(
        major="Computer Science",
        minor="Artificial Intelligence",
        interests=["Machine Learning"],
        db=db_session,
    )
    assert result["items"][0]["id"] != legacy_id
    assert result["items"][0]["education_level_id"] is None

    stored = (
        await db_session.execute(select(AcademicInterest).where(AcademicInterest.id == legacy_id))
    ).scalar_one()
    assert stored.education_level_id == 1
    assert stored.major_id is None


@pytest.mark.asyncio
async def test_unrelated_minor_is_allowed_when_global_catalog(db_session) -> None:
    await bulk_create_majors([CatalogNameItem(name="Mechanical Engineering")], db_session)
    await bulk_create_minors([CatalogNameItem(name="Artificial Intelligence")], db_session)
    result = await create_user_academic_interests(
        major="Mechanical Engineering",
        minor="Artificial Intelligence",
        interests=["Robotics"],
        db=db_session,
    )
    assert result["items"][0]["name"] == "Robotics"
    assert result["minor"]["name"] == "Artificial Intelligence"


@pytest.mark.asyncio
async def test_user_interest_response_includes_catalog_added_by(db_session) -> None:
    await bulk_create_majors([CatalogNameItem(name="Computer Science")], db_session)
    await bulk_create_minors([CatalogNameItem(name="Artificial Intelligence")], db_session)
    major = (
        await db_session.execute(select(Major).where(Major.name == "Computer Science"))
    ).scalar_one()
    minor = (
        await db_session.execute(
            select(Minor).where(Minor.name == "Artificial Intelligence")
        )
    ).scalar_one()
    major.major_added_by = InterestAddedBy.user.value
    minor.minor_added_by = InterestAddedBy.user.value
    db_session.add(major)
    db_session.add(minor)
    await db_session.commit()

    result = await create_user_academic_interests(
        major="Computer Science",
        minor="Artificial Intelligence",
        interests=["Machine Learning"],
        db=db_session,
    )
    assert result["major"]["major_added_by"] == "user"
    assert result["minor"]["minor_added_by"] == "user"
    assert result["items"][0]["interest_added_by"] == "user"


@pytest.mark.asyncio
async def test_transaction_rollback_when_flush_fails(db_session) -> None:
    await bulk_create_majors([CatalogNameItem(name="Computer Science")], db_session)

    async def _boom() -> None:
        raise IntegrityError("insert", {}, Exception("duplicate"))

    db_session.flush = _boom  # type: ignore[method-assign]
    with pytest.raises(ApiError, match="Duplicate academic interest combination"):
        await create_user_academic_interests(
            major="Computer Science",
            minor=None,
            interests=["Algorithms"],
            db=db_session,
        )
    rows = list((await db_session.execute(select(AcademicInterest))).scalars().all())
    assert rows == []
