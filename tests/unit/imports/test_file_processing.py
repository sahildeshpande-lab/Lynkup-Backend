from __future__ import annotations

import pytest

from apps.imports.enums import ImportType
from apps.imports.file_parser import MAX_IMPORT_BYTES, read_import_dataframe
from common.exceptions import ApiError
from tests.unit.imports.conftest import csv_bytes, make_upload, run_import, xlsx_bytes


def test_csv_and_xlsx_headers_are_trimmed() -> None:
    csv_df = read_import_dataframe(
        "majors.csv",
        csv_bytes([" name "], [["Biology"]]),
    )
    assert list(csv_df.columns) == ["name"]

    xlsx_df = read_import_dataframe(
        "majors.xlsx",
        xlsx_bytes([" name "], [["Biology"]]),
    )
    assert list(xlsx_df.columns) == ["name"]


def test_resolve_catalog_names_matches_misspellings_and_clusters_new_names() -> None:
    from apps.imports.normalization import match_existing_name, resolve_catalog_names

    existing = {"computer science": "Computer Science"}
    assert match_existing_name("Computre Science", existing) == "Computer Science"
    assert match_existing_name("Biology", existing) is None

    resolved = resolve_catalog_names(
        ["Computre Science", "Mechanical Engineering", "Mechanical Enginering"],
        existing,
    )
    assert resolved["computre science"] == "Computer Science"
    assert resolved["mechanical engineering"] == "Mechanical Engineering"
    assert resolved["mechanical enginering"] == "Mechanical Engineering"


def test_unsupported_extension_is_rejected() -> None:
    with pytest.raises(ApiError, match="Unsupported file type"):
        read_import_dataframe("majors.xls", b"name\nA\n")
    with pytest.raises(ApiError, match="Unsupported file type"):
        read_import_dataframe("majors.txt", b"hello")


def test_empty_file_is_rejected() -> None:
    with pytest.raises(ApiError, match="File is empty"):
        read_import_dataframe("majors.csv", b"")


def test_headers_only_file_is_rejected() -> None:
    with pytest.raises(ApiError, match="File has no data rows"):
        read_import_dataframe("majors.csv", csv_bytes(["name"], []))


def test_corrupted_xlsx_is_rejected() -> None:
    with pytest.raises(ApiError, match="Unable to read file"):
        read_import_dataframe("majors.xlsx", b"this is not an excel file")


def test_oversized_file_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr("apps.imports.file_parser.MAX_IMPORT_BYTES", 8)
    with pytest.raises(ApiError, match="File size exceeds maximum"):
        read_import_dataframe("majors.csv", b"0123456789")
    assert MAX_IMPORT_BYTES == 10 * 1024 * 1024


@pytest.mark.asyncio
async def test_csv_and_xlsx_import_succeed(db_session) -> None:
    csv_result = await run_import(
        db_session,
        ImportType.MAJOR,
        "majors.csv",
        csv_bytes(["name"], [["Accounting"]]),
    )
    assert csv_result["successfulCount"] == 1
    assert csv_result["fileName"] == "majors.csv"

    xlsx_result = await run_import(
        db_session,
        ImportType.MAJOR,
        "majors.xlsx",
        xlsx_bytes(["name"], [["Computer Science"]]),
    )
    assert xlsx_result["successfulCount"] == 1
    assert xlsx_result["fileName"] == "majors.xlsx"


@pytest.mark.asyncio
async def test_unsupported_extension_via_service(db_session) -> None:
    with pytest.raises(ApiError, match="Unsupported file type"):
        await run_import(db_session, ImportType.MAJOR, "majors.pdf", b"%PDF")


@pytest.mark.asyncio
async def test_empty_upload_via_service(db_session) -> None:
    with pytest.raises(ApiError, match="File is empty"):
        await import_upload_empty(db_session)


async def import_upload_empty(db_session):
    from apps.imports.services import import_upload

    return await import_upload(
        import_type=ImportType.MAJOR,
        file=make_upload("majors.csv", b""),
        db=db_session,
    )
