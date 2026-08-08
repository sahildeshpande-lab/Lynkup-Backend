from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

_TMP_ROOT = Path(__file__).resolve().parents[3] / "storage" / ".pytest_tmp"


@pytest.fixture
def export_tmp_path():
    """Project-local temp directory (avoids Windows pytest tmp PermissionError)."""
    _TMP_ROOT.mkdir(parents=True, exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix="export_", dir=_TMP_ROOT))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
