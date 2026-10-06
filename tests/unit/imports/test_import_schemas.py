from __future__ import annotations

from apps.imports.enums import ImportType
from apps.imports.schemas import ImportResultData, ImportRowIssue


def test_import_row_issue_model() -> None:
    issue = ImportRowIssue(row=2, reason="duplicate email")
    assert issue.row == 2
    assert issue.reason == "duplicate email"


def test_import_result_data_defaults() -> None:
    result = ImportResultData(
        type=ImportType.UNIVERSITY,
        fileName="universities.csv",
        totalRecords=10,
        successfulCount=8,
        duplicateCount=1,
        failedCount=1,
    )
    assert result.failedRows == []
    assert result.duplicateRows == []
