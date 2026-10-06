from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from apps.profiles.db_models.country_db_model import Country
from apps.search.services import list_countries


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
async def test_list_countries_defaults_to_name_a_to_z(db_session) -> None:
    now = datetime(2026, 3, 1, tzinfo=timezone.utc)
    db_session.add_all(
        [
            Country(name="Canada", iso_code="CA", created_at=now, updated_at=now),
            Country(name="Australia", iso_code="AU", created_at=now, updated_at=now),
            Country(name="Brazil", iso_code="BR", created_at=now, updated_at=now),
        ]
    )
    await db_session.commit()

    result = await list_countries(
        query=None,
        page=1,
        page_size=10,
        db=db_session,
        sort=None,
        order="asc",
    )

    assert [item["name"] for item in result["items"]] == ["Australia", "Brazil", "Canada"]


@pytest.mark.asyncio
async def test_list_countries_name_z_to_a_when_order_desc(db_session) -> None:
    now = datetime(2026, 3, 1, tzinfo=timezone.utc)
    db_session.add_all(
        [
            Country(name="Canada", iso_code="CA", created_at=now, updated_at=now),
            Country(name="Australia", iso_code="AU", created_at=now, updated_at=now),
            Country(name="Brazil", iso_code="BR", created_at=now, updated_at=now),
        ]
    )
    await db_session.commit()

    result = await list_countries(
        query=None,
        page=1,
        page_size=10,
        db=db_session,
        sort=None,
        order="desc",
    )

    assert [item["name"] for item in result["items"]] == ["Canada", "Brazil", "Australia"]


@pytest.mark.asyncio
async def test_list_countries_sorts_by_last_activity_desc(db_session) -> None:
    now = datetime(2026, 3, 1, tzinfo=timezone.utc)
    # created oldest, but updated most recently → should rank first on last activity desc
    first = Country(
        name="First",
        iso_code="AA",
        created_at=now - timedelta(days=10),
        updated_at=now,
    )
    second = Country(
        name="Second",
        iso_code="BB",
        created_at=now - timedelta(days=1),
        updated_at=now - timedelta(days=1),
    )
    third = Country(
        name="Third",
        iso_code="CC",
        created_at=now - timedelta(days=5),
        updated_at=now - timedelta(days=5),
    )
    inactive = Country(
        name="Inactive",
        iso_code="XX",
        is_active=False,
        created_at=now + timedelta(days=1),
        updated_at=now + timedelta(days=1),
    )
    db_session.add_all([first, second, third, inactive])
    await db_session.commit()

    result = await list_countries(
        query=None,
        page=1,
        page_size=10,
        db=db_session,
        sort="created_at",
        order="desc",
    )

    assert [item["name"] for item in result["items"]] == ["First", "Second", "Third"]
    assert result["totalItems"] == 3


@pytest.mark.asyncio
async def test_list_countries_sorts_by_last_activity_asc(db_session) -> None:
    now = datetime(2026, 3, 1, tzinfo=timezone.utc)
    first = Country(
        name="First",
        iso_code="AA",
        created_at=now - timedelta(days=10),
        updated_at=now - timedelta(days=10),
    )
    second = Country(
        name="Second",
        iso_code="BB",
        created_at=now - timedelta(days=1),
        updated_at=now - timedelta(days=1),
    )
    third = Country(
        name="Third",
        iso_code="CC",
        created_at=now - timedelta(days=5),
        updated_at=now,
    )
    db_session.add_all([third, first, second])
    await db_session.commit()

    result = await list_countries(
        query=None,
        page=1,
        page_size=10,
        db=db_session,
        sort="created_at",
        order="asc",
    )

    assert [item["name"] for item in result["items"]] == ["First", "Second", "Third"]
