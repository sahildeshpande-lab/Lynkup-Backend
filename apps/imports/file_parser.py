from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pandas as pd

from common.exceptions import ApiError

MAX_IMPORT_BYTES = 10 * 1024 * 1024
MAX_IMPORT_ROWS = 10_000
ALLOWED_EXTENSIONS = {".csv", ".xlsx"}


def _unsupported_file_message() -> str:
    return "Unsupported file type. Only .csv and .xlsx files are allowed"


def read_import_dataframe(filename: str | None, content: bytes) -> pd.DataFrame:
    if not content:
        raise ApiError("File is empty")
    if len(content) > MAX_IMPORT_BYTES:
        raise ApiError("File size exceeds maximum allowed limit of 10 MB")

    extension = Path(filename or "").suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ApiError(_unsupported_file_message())

    buffer = BytesIO(content)
    try:
        if extension == ".csv":
            dataframe = pd.read_csv(buffer, encoding="utf-8-sig")
        else:
            dataframe = pd.read_excel(buffer, engine="openpyxl")
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError("Unable to read file. The file may be corrupted or unreadable") from exc

    if dataframe is None:
        raise ApiError("Unable to read file. The file may be corrupted or unreadable")

    dataframe.columns = [
        str(column).strip() if column is not None else "" for column in dataframe.columns
    ]
    if any(not column for column in dataframe.columns):
        dataframe.columns = [
            column if column else f"unnamed_{index}"
            for index, column in enumerate(dataframe.columns)
        ]

    if dataframe.empty:
        raise ApiError("File has no data rows")
    if len(dataframe.index) > MAX_IMPORT_ROWS:
        raise ApiError(f"File exceeds maximum of {MAX_IMPORT_ROWS} rows")
    return dataframe


def require_columns(dataframe: pd.DataFrame, required: tuple[str, ...]) -> None:
    present = {str(column).strip().lower() for column in dataframe.columns}
    missing = [column for column in required if column.lower() not in present]
    if missing:
        raise ApiError(f"Missing required columns: {', '.join(missing)}")


def iter_records(dataframe: pd.DataFrame) -> list[tuple[int, dict]]:
    records: list[tuple[int, dict]] = []
    columns = list(dataframe.columns)
    for index, series in dataframe.iterrows():
        row_number = int(index) + 2
        raw = {str(column): series[column] for column in columns}
        if all(_is_empty_cell(value) for value in raw.values()):
            continue
        records.append((row_number, raw))
    if not records:
        raise ApiError("File has no data rows")
    return records


def _is_empty_cell(value) -> bool:
    try:
        return value is None or (not isinstance(value, (str, bytes)) and pd.isna(value))
    except (TypeError, ValueError):
        return False
