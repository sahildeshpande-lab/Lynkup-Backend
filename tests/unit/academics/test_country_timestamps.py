from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

from apps.academics.schemas import AcademicCatalogPatchRequest, AcademicCatalogType, CountryAdminItem
from apps.academics.services import bulk_create_countries, patch_catalog_record
from apps.profiles.db_models.country_db_model import Country


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def _prepare_sqlite():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: SQLModel.metadata.create_all(
                sync_conn, tables=[Country.__table__]
            )
        )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


@pytest_asyncio.fixture
async def db_session():
    engine, factory = await _prepare_sqlite()
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_new_country_timestamps_use_database_now(db_session):
    before = datetime.now(timezone.utc) - timedelta(seconds=2)
    iso_code = "QZ"
    result = await bulk_create_countries(
        [CountryAdminItem(name=f"Catalogland {''.join(c for c in uuid4().hex if c.isalpha())[:6]}", iso_code=iso_code)],
        db_session,
    )
    after = datetime.now(timezone.utc) + timedelta(seconds=2)

    assert result["created"] == 1
    country_id = UUID(result["items"][0]["id"])
    row = (
        await db_session.execute(select(Country).where(Country.id == country_id))
    ).scalar_one()
    await db_session.refresh(row)

    created_at = _as_utc(row.created_at)
    updated_at = _as_utc(row.updated_at)
    assert before <= created_at <= after
    assert before <= updated_at <= after


@pytest.mark.asyncio
async def test_country_update_changes_updated_at_not_created_at(db_session):
    result = await bulk_create_countries(
        [CountryAdminItem(name="India", iso_code="IN")],
        db_session,
    )
    country_id = UUID(result["items"][0]["id"])
    row = (
        await db_session.execute(select(Country).where(Country.id == country_id))
    ).scalar_one()
    await db_session.refresh(row)
    created_before = _as_utc(row.created_at)
    updated_before = _as_utc(row.updated_at)

    await patch_catalog_record(
        AcademicCatalogType.country,
        str(country_id),
        {"name": "Republic of India"},
        db_session,
    )
    await db_session.refresh(row)

    assert _as_utc(row.created_at) == created_before
    assert _as_utc(row.updated_at) > updated_before
    assert row.name == "Republic of India"


@pytest.mark.asyncio
async def test_country_request_payload_cannot_override_timestamps():
    with pytest.raises(ValidationError):
        AcademicCatalogPatchRequest.model_validate(
            {
                "type": "country",
                "id": str(uuid4()),
                "data": {
                    "name": "India",
                    "created_at": "2000-01-01T00:00:00+00:00",
                    "updated_at": "2000-01-01T00:00:00+00:00",
                },
            }
        )

    with pytest.raises(ValidationError):
        AcademicCatalogPatchRequest.model_validate(
            {
                "type": "country",
                "id": str(uuid4()),
                "data": {
                    "name": "India",
                    "createdAt": "2000-01-01T00:00:00+00:00",
                    "updatedAt": "2000-01-01T00:00:00+00:00",
                },
            }
        )


def test_country_admin_item_ignores_client_timestamps():
    item = CountryAdminItem.model_validate(
        {
            "name": "India",
            "iso_code": "IN",
            "created_at": "2000-01-01T00:00:00+00:00",
            "updated_at": "2000-01-01T00:00:00+00:00",
        }
    )
    assert item.name == "India"
    assert item.iso_code == "IN"
    assert "created_at" not in CountryAdminItem.model_fields
    assert "updated_at" not in CountryAdminItem.model_fields
    country = Country(name=item.name, iso_code=item.iso_code, is_active=item.is_active)
    assert country.name == "India"
    assert country.iso_code == "IN"
    assert country.created_at is None
    assert country.updated_at is None
