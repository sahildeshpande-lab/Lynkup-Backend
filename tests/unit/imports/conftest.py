from __future__ import annotations

import csv
from io import BytesIO, StringIO

import pandas as pd
import pytest
import pytest_asyncio
from fastapi import UploadFile
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

if not hasattr(SQLiteTypeCompiler, "visit_JSONB"):
    def _visit_jsonb(self, type_, **kw):  # noqa: ANN001
        return self.visit_JSON(type_, **kw)

    SQLiteTypeCompiler.visit_JSONB = _visit_jsonb  # type: ignore[attr-defined, assignment]


from apps.imports.enums import ImportType
from apps.imports.services import import_upload
from apps.moderation.db_models.moderation_words_db_model import ModerationWordsConfig
from apps.profiles.db_models.academic_interests_db_model import AcademicInterest
from apps.profiles.db_models.country_db_model import Country
from apps.profiles.db_models.education_level_db_model import EducationLevel
from apps.profiles.db_models.major_db_model import Major
from apps.profiles.db_models.minor_db_model import Minor
from apps.profiles.db_models.university_db_model import University


def csv_bytes(headers: list[str], rows: list[list]) -> bytes:
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def xlsx_bytes(headers: list[str], rows: list[list]) -> bytes:
    buffer = BytesIO()
    pd.DataFrame(rows, columns=headers).to_excel(buffer, index=False, engine="openpyxl")
    return buffer.getvalue()


def make_upload(filename: str, data: bytes) -> UploadFile:
    return UploadFile(filename=filename, file=BytesIO(data))


async def run_import(
    db,
    import_type: ImportType,
    filename: str,
    data: bytes,
):
    return await import_upload(
        import_type=import_type,
        file=make_upload(filename, data),
        db=db,
    )


def assert_counts(result: dict, *, successful: int, duplicate: int, failed: int) -> None:
    assert result["successfulCount"] == successful
    assert result["duplicateCount"] == duplicate
    assert result["failedCount"] == failed
    assert result["totalRecords"] == successful + duplicate + failed


async def _prepare_sqlite():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    tables = [
        Major.__table__,
        Minor.__table__,
        EducationLevel.__table__,
        AcademicInterest.__table__,
        Country.__table__,
        University.__table__,
        ModerationWordsConfig.__table__,
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


@pytest.fixture
def anyio_backend():
    return "asyncio"
