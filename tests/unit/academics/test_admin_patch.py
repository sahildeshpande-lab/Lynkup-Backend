from __future__ import annotations

from uuid import UUID, uuid4
from datetime import datetime, timezone, date

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

# Workaround for SQLite rendering PostgreSQL-specific JSONB type
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB

@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(type_, compiler, **kw):
    return "JSON"


from apps.academics.schemas import AcademicCatalogType
from apps.accounts.db_models import User, Role, Permission, UserRole, RolePermission
from apps.engagement.db_models.bookmark_db_model import Bookmark
from apps.engagement.db_models.comment_db_model import Comment
from apps.engagement.db_models.comment_reaction_db_model import CommentReaction
from apps.engagement.db_models.share_event_db_model import ShareEvent
from apps.profiles.db_models.academic_interests_db_model import AcademicInterest
from apps.profiles.db_models.country_db_model import Country
from apps.profiles.db_models.education_level_db_model import EducationLevel
from apps.profiles.db_models.major_db_model import Major
from apps.profiles.db_models.minor_db_model import Minor
from apps.profiles.db_models.university_db_model import University
from apps.profiles.db_models.profile_db_model import Profile
from common.exceptions import ApiError
from core.database.session import get_session
from core.security.auth import get_current_admin
from entrypoints.api import app
from apps.administration.dependencies import require_signed_admin


async def _prepare_sqlite():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    tables = [
        User.__table__,
        Role.__table__,
        UserRole.__table__,
        RolePermission.__table__,
        Permission.__table__,
        Profile.__table__,
        Major.__table__,
        Minor.__table__,
        EducationLevel.__table__,
        AcademicInterest.__table__,
        Country.__table__,
        University.__table__,
        Bookmark.__table__,
        Comment.__table__,
        CommentReaction.__table__,
        ShareEvent.__table__,
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


@pytest_asyncio.fixture
async def app_client(db_session):
    async def _override_session():
        yield db_session

    async def _override_admin():
        user = User(
            id=uuid4(),
            email="admin@example.com",
            firebase_uid="admin-uid",
        )
        user.role = "superadmin"
        user.status = "active"
        return user

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_admin] = _override_admin
    app.dependency_overrides[require_signed_admin] = _override_admin
    yield TestClient(app)
    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(get_current_admin, None)
    app.dependency_overrides.pop(require_signed_admin, None)


@pytest.mark.asyncio
async def test_patch_major_name(db_session, app_client) -> None:
    major = Major(name="Accounting", is_active=True)
    db_session.add(major)
    await db_session.commit()
    await db_session.refresh(major)

    created_at_before = major.created_at
    updated_at_before = major.updated_at

    response = app_client.patch(
        "/api/v1/admin/academics",
        json={
            "type": "major",
            "id": str(major.id),
            "data": {"name": "Accounting and Finance"},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"].lower() == "major updated successfully"
    data = body["data"]
    assert data["total"] == 1
    assert data["created"] == 1
    assert data["existing"] == 0
    assert data["failed"] == 0
    assert data["total"] == data["created"] + data["existing"] + data["failed"]
    assert data["already_existing"] == []
    assert data["failures"] == []
    assert len(data["items"]) == 1
    assert data["items"][0]["name"] == "Accounting And Finance"

    # Refresh and check db
    await db_session.refresh(major)
    assert major.name == "Accounting And Finance"
    assert major.created_at == created_at_before
    assert major.updated_at > updated_at_before


@pytest.mark.asyncio
async def test_patch_major_is_active(db_session, app_client) -> None:
    major = Major(name="Physics", is_active=True)
    db_session.add(major)
    await db_session.commit()
    await db_session.refresh(major)

    response = app_client.patch(
        "/api/v1/admin/academics",
        json={
            "type": "major",
            "id": str(major.id),
            "data": {"isActive": False},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["items"][0]["isActive"] is False

    await db_session.refresh(major)
    assert major.is_active is False


@pytest.mark.asyncio
async def test_patch_minor_name(db_session, app_client) -> None:
    minor = Minor(name="Music", is_active=True)
    db_session.add(minor)
    await db_session.commit()
    await db_session.refresh(minor)

    response = app_client.patch(
        "/api/v1/admin/academics",
        json={
            "type": "minor",
            "id": str(minor.id),
            "data": {"name": "Classical Music"},
        },
    )
    assert response.status_code == 200
    assert response.json()["data"]["items"][0]["name"] == "Classical Music"


@pytest.mark.asyncio
async def test_patch_minor_is_active(db_session, app_client) -> None:
    minor = Minor(name="History", is_active=True)
    db_session.add(minor)
    await db_session.commit()
    await db_session.refresh(minor)

    response = app_client.patch(
        "/api/v1/admin/academics",
        json={
            "type": "minor",
            "id": str(minor.id),
            "data": {"is_active": False},
        },
    )
    assert response.status_code == 200
    assert response.json()["data"]["items"][0]["isActive"] is False


@pytest.mark.asyncio
async def test_patch_academic_interest_name(db_session, app_client) -> None:
    major = Major(name="Computer Science")
    db_session.add(major)
    await db_session.commit()
    await db_session.refresh(major)

    interest = AcademicInterest(name="Algorithms", major_id=major.id)
    db_session.add(interest)
    await db_session.commit()
    await db_session.refresh(interest)

    response = app_client.patch(
        "/api/v1/admin/academics",
        json={
            "type": "academic_interest",
            "id": str(interest.id),
            "data": {"name": "Advanced Algorithms"},
        },
    )
    assert response.status_code == 200
    assert response.json()["data"]["items"][0]["name"] == "Advanced Algorithms"


@pytest.mark.asyncio
async def test_patch_academic_interest_major_id(db_session, app_client) -> None:
    major1 = Major(name="Math")
    major2 = Major(name="Science")
    db_session.add_all([major1, major2])
    await db_session.commit()
    await db_session.refresh(major1)
    await db_session.refresh(major2)

    interest = AcademicInterest(name="Calculus", major_id=major1.id)
    db_session.add(interest)
    await db_session.commit()
    await db_session.refresh(interest)

    response = app_client.patch(
        "/api/v1/admin/academics",
        json={
            "type": "academic_interest",
            "id": str(interest.id),
            "data": {"majorId": major2.id},
        },
    )
    assert response.status_code == 200
    assert response.json()["data"]["items"][0]["majorId"] == str(major2.id)


@pytest.mark.asyncio
async def test_patch_academic_interest_minor_id(db_session, app_client) -> None:
    major = Major(name="Engineering")
    minor = Minor(name="Business")
    db_session.add_all([major, minor])
    await db_session.commit()
    await db_session.refresh(major)
    await db_session.refresh(minor)

    interest = AcademicInterest(name="Management", major_id=major.id)
    db_session.add(interest)
    await db_session.commit()
    await db_session.refresh(interest)

    response = app_client.patch(
        "/api/v1/admin/academics",
        json={
            "type": "academic_interest",
            "id": str(interest.id),
            "data": {"minorId": minor.id},
        },
    )
    assert response.status_code == 200
    assert response.json()["data"]["items"][0]["minorId"] == str(minor.id)


@pytest.mark.asyncio
async def test_patch_academic_interest_minor_id_to_null(db_session, app_client) -> None:
    major = Major(name="Engineering")
    minor = Minor(name="Business")
    db_session.add_all([major, minor])
    await db_session.commit()
    await db_session.refresh(major)
    await db_session.refresh(minor)

    interest = AcademicInterest(name="Management", major_id=major.id, minor_id=minor.id)
    db_session.add(interest)
    await db_session.commit()
    await db_session.refresh(interest)

    response = app_client.patch(
        "/api/v1/admin/academics",
        json={
            "type": "academic_interest",
            "id": str(interest.id),
            "data": {"minor_id": None},
        },
    )
    assert response.status_code == 200
    assert response.json()["data"]["items"][0]["minorId"] is None

    await db_session.refresh(interest)
    assert interest.minor_id is None


@pytest.mark.asyncio
async def test_patch_academic_interest_reject_minor_only(db_session, app_client) -> None:
    major = Major(name="Engineering")
    minor = Minor(name="Business")
    db_session.add_all([major, minor])
    await db_session.commit()
    await db_session.refresh(major)
    await db_session.refresh(minor)

    interest = AcademicInterest(name="Management", major_id=major.id)
    db_session.add(interest)
    await db_session.commit()
    await db_session.refresh(interest)

    # Attempt to set major_id to None
    response = app_client.patch(
        "/api/v1/admin/academics",
        json={
            "type": "academic_interest",
            "id": str(interest.id),
            "data": {"major_id": None, "minor_id": minor.id},
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] is False
    assert "major_id is required" in response.json()["message"]


@pytest.mark.asyncio
async def test_patch_academic_interest_preserve_education_level_id(db_session, app_client) -> None:
    major = Major(name="Engineering")
    edu_level = EducationLevel(id=1, name="Bachelor")
    db_session.add_all([major, edu_level])
    await db_session.commit()
    await db_session.refresh(major)
    await db_session.refresh(edu_level)

    interest = AcademicInterest(name="Management", major_id=major.id, education_level_id=edu_level.id)
    db_session.add(interest)
    await db_session.commit()
    await db_session.refresh(interest)

    response = app_client.patch(
        "/api/v1/admin/academics",
        json={
            "type": "academic_interest",
            "id": str(interest.id),
            "data": {"name": "Project Management"},
        },
    )
    assert response.status_code == 200
    assert response.json()["data"]["items"][0]["educationLevelId"] == str(edu_level.id)

    await db_session.refresh(interest)
    assert interest.education_level_id == edu_level.id


@pytest.mark.asyncio
async def test_patch_academic_interest_preserve_interest_added_by(db_session, app_client) -> None:
    major = Major(name="Engineering")
    db_session.add(major)
    await db_session.commit()
    await db_session.refresh(major)

    interest = AcademicInterest(name="Management", major_id=major.id, interest_added_by="user")
    db_session.add(interest)
    await db_session.commit()
    await db_session.refresh(interest)

    response = app_client.patch(
        "/api/v1/admin/academics",
        json={
            "type": "academic_interest",
            "id": str(interest.id),
            "data": {"name": "Project Management"},
        },
    )
    assert response.status_code == 200

    await db_session.refresh(interest)
    assert interest.interest_added_by == "user"


@pytest.mark.asyncio
async def test_patch_university_fields(db_session, app_client) -> None:
    country1 = Country(name="India", iso_code="IN")
    country2 = Country(name="United States", iso_code="US")
    db_session.add_all([country1, country2])
    await db_session.commit()
    await db_session.refresh(country1)
    await db_session.refresh(country2)

    univ = University(
        name="Old Univ",
        slug="old-univ",
        country_id=country1.id,
        website="https://old.edu",
        major=[{"name": "History"}],
        minor=[{"name": "Math"}],
        academic_program=[{"name": "B.A."}],
        is_active=True,
    )
    db_session.add(univ)
    await db_session.commit()
    await db_session.refresh(univ)

    response = app_client.patch(
        "/api/v1/admin/academics",
        json={
            "type": "university",
            "id": str(univ.id),
            "data": {
                "name": "New Univ",
                "slug": "new-univ",
                "countryId": str(country2.id),
                "website": "https://new.edu",
                "major": ["CS"],
                "minor": ["AI"],
                "academicProgram": ["B.S."],
                "isActive": False,
            },
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["items"][0]["name"] == "New Univ"
    assert body["data"]["items"][0]["slug"] == "new-univ"
    assert body["data"]["items"][0]["countryId"] == str(country2.id)
    assert body["data"]["items"][0]["website"] == "https://new.edu"
    assert body["data"]["items"][0]["major"] == [{"name": "Cs"}]
    assert body["data"]["items"][0]["minor"] == [{"name": "Ai"}]
    assert body["data"]["items"][0]["academicProgram"] == [{"name": "B.S."}]
    assert body["data"]["items"][0]["isActive"] is False

    await db_session.refresh(univ)
    assert univ.name == "New Univ"
    assert univ.country_id == country2.id
    assert univ.is_active is False


@pytest.mark.asyncio
async def test_patch_country_fields(db_session, app_client) -> None:
    country = Country(name="India", iso_code="IN")
    db_session.add(country)
    await db_session.commit()
    await db_session.refresh(country)

    response = app_client.patch(
        "/api/v1/admin/academics",
        json={
            "type": "country",
            "id": str(country.id),
            "data": {
                "name": "Republic of India",
                "isoCode": "ID",
                "isActive": False,
            },
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["items"][0]["name"] == "Republic of India"
    assert body["data"]["items"][0]["isoCode"] == "ID"
    assert body["data"]["items"][0]["isActive"] is False

    await db_session.refresh(country)
    assert country.name == "Republic of India"
    assert country.iso_code == "ID"
    assert country.is_active is False


@pytest.mark.asyncio
async def test_patch_partial_update(db_session, app_client) -> None:
    country = Country(name="India", iso_code="IN")
    db_session.add(country)
    await db_session.commit()
    await db_session.refresh(country)

    univ = University(
        name="Example Univ",
        slug="example-univ",
        country_id=country.id,
        website="https://example.edu",
        major=[{"name": "History"}],
        is_active=True,
    )
    db_session.add(univ)
    await db_session.commit()
    await db_session.refresh(univ)

    response = app_client.patch(
        "/api/v1/admin/academics",
        json={
            "type": "university",
            "id": str(univ.id),
            "data": {"website": "https://newwebsite.edu"},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["items"][0]["website"] == "https://newwebsite.edu"
    assert body["data"]["items"][0]["name"] == "Example Univ"
    assert body["data"]["items"][0]["slug"] == "example-univ"
    assert body["data"]["items"][0]["major"] == [{"name": "History"}]
    assert body["data"]["items"][0]["isActive"] is True

    await db_session.refresh(univ)
    assert univ.website == "https://newwebsite.edu"
    assert univ.name == "Example Univ"


@pytest.mark.asyncio
async def test_patch_empty_data(db_session, app_client) -> None:
    major = Major(name="Accounting")
    db_session.add(major)
    await db_session.commit()
    await db_session.refresh(major)

    response = app_client.patch(
        "/api/v1/admin/academics",
        json={
            "type": "major",
            "id": str(major.id),
            "data": {},
        },
    )
    # Validation error because data is empty
    assert response.status_code == 200
    assert response.json()["status"] is False
    assert "At least one editable field" in response.json()["message"]


@pytest.mark.asyncio
async def test_patch_invalid_type(db_session, app_client) -> None:
    response = app_client.patch(
        "/api/v1/admin/academics",
        json={
            "type": "invalid_type",
            "id": "1",
            "data": {"name": "New"},
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] is False


@pytest.mark.asyncio
async def test_patch_invalid_id(db_session, app_client) -> None:
    response = app_client.patch(
        "/api/v1/admin/academics",
        json={
            "type": "major",
            "id": "99999",
            "data": {"name": "New"},
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] is False
    assert "Major not found" in response.json()["message"]


@pytest.mark.asyncio
async def test_patch_duplicate_conflict(db_session, app_client) -> None:
    # 1. Major
    major1 = Major(name="CS")
    major2 = Major(name="Math")
    db_session.add_all([major1, major2])
    await db_session.commit()
    await db_session.refresh(major1)
    await db_session.refresh(major2)

    response = app_client.patch(
        "/api/v1/admin/academics",
        json={
            "type": "major",
            "id": str(major2.id),
            "data": {"name": "CS"},
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] is False
    assert "Duplicate catalog name" in response.json()["message"]

    # 2. Country
    country1 = Country(name="India", iso_code="IN")
    country2 = Country(name="US", iso_code="US")
    db_session.add_all([country1, country2])
    await db_session.commit()
    await db_session.refresh(country1)
    await db_session.refresh(country2)

    response = app_client.patch(
        "/api/v1/admin/academics",
        json={
            "type": "country",
            "id": str(country2.id),
            "data": {"iso_code": "IN"},
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] is False
    assert "Duplicate country iso_code" in response.json()["message"]


@pytest.mark.asyncio
async def test_patch_unauthorized(db_session) -> None:
    # Override current_admin dependency to raise insufficient permissions
    async def _override_non_admin():
        raise ApiError("Insufficient permissions")

    async def _override_session():
        yield db_session

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_admin] = _override_non_admin
    app.dependency_overrides[require_signed_admin] = _override_non_admin

    client_non_admin = TestClient(app)
    response = client_non_admin.patch(
        "/api/v1/admin/academics",
        json={
            "type": "major",
            "id": "1",
            "data": {"name": "New"},
        },
    )
    assert response.status_code == 403
    assert response.json()["status"] is False
    assert "Insufficient permissions" in response.json()["message"]

    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(get_current_admin, None)
    app.dependency_overrides.pop(require_signed_admin, None)


@pytest.mark.asyncio
async def test_patch_verify_updated_at_changes(db_session, app_client) -> None:
    major = Major(name="Accounting")
    db_session.add(major)
    await db_session.commit()
    await db_session.refresh(major)

    updated_at_before = major.updated_at

    response = app_client.patch(
        "/api/v1/admin/academics",
        json={
            "type": "major",
            "id": str(major.id),
            "data": {"name": "Accounting and Business"},
        },
    )
    assert response.status_code == 200

    await db_session.refresh(major)
    assert major.updated_at > updated_at_before


@pytest.mark.asyncio
async def test_patch_verify_created_at_does_not_change(db_session, app_client) -> None:
    major = Major(name="Accounting")
    db_session.add(major)
    await db_session.commit()
    await db_session.refresh(major)

    created_at_before = major.created_at

    response = app_client.patch(
        "/api/v1/admin/academics",
        json={
            "type": "major",
            "id": str(major.id),
            "data": {"name": "Accounting and Business"},
        },
    )
    assert response.status_code == 200

    await db_session.refresh(major)
    assert major.created_at == created_at_before


@pytest.mark.asyncio
async def test_patch_verify_existing_user_profile_unchanged(db_session, app_client) -> None:
    # Use dummy user ID in profile instead of inserting a User object,
    # because inserting a User triggers selectin relationship loading on tables not fully mockable in SQLite.
    major = Major(name="CS")
    db_session.add(major)
    await db_session.commit()
    await db_session.refresh(major)

    profile = Profile(
        user_id=uuid4(),
        first_name="John",
        last_name="Doe",
        major="CS",
        major_id=major.id,
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)

    # Patch the Major's name
    response = app_client.patch(
        "/api/v1/admin/academics",
        json={
            "type": "major",
            "id": str(major.id),
            "data": {"name": "Computer Science"},
        },
    )
    assert response.status_code == 200

    # Verify Profile remains completely unchanged
    await db_session.refresh(profile)
    assert profile.major == "CS"
    assert profile.major_id == major.id
    assert profile.first_name == "John"
