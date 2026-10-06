"""Unit tests for uploaded-filename normalization."""

from __future__ import annotations

import pytest

from common.filenames import normalize_filename


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Java%20notes.pdf", "Java notes.pdf"),
        (
            "Star%20Pattern%20Programs%20in%20Java%20%F0%9F%92%A1.pdf",
            "Star Pattern Programs in Java 💡.pdf",
        ),
        ("Java notes.pdf", "Java notes.pdf"),
        ("Resume%20%282026%29.pdf", "Resume (2026).pdf"),
        (None, None),
        ("", ""),
    ],
)
def test_normalize_filename(raw: str | None, expected: str | None) -> None:
    assert normalize_filename(raw) == expected


@pytest.mark.parametrize(
    "malformed",
    [
        "bad%ZZ.pdf",
        "incomplete%.pdf",
        "ends-with%",
        "%",
        "file%2",
        "100%.pdf",
        "notes%2G.pdf",
    ],
)
def test_normalize_filename_malformed_percent_encoding_does_not_crash(
    malformed: str,
) -> None:
    result = normalize_filename(malformed)
    assert result is not None
    assert isinstance(result, str)
    assert result == malformed
