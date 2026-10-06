from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy.orm import configure_mappers
from sqlmodel import select

from apps.export.models import DataExportRequest


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_export_select_compiles_after_mapper_configuration():
    configure_mappers()
    compiled = str(select(DataExportRequest).compile())
    assert "data_export_requests" in compiled.lower()


def test_celery_worker_registers_invitation_before_user_mapper_config():
    """Fresh interpreter: Celery boot path must register Invitation before User maps.

    Unit conftest already imports Invitation, so this subprocess is the
    regression that matches the independent Celery worker process.
    """
    env = os.environ.copy()
    env.setdefault("CELERY_BROKER_URL", "redis://localhost:6379/0")
    env.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    env.setdefault("DISABLE_DB_POOL", "true")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_ROOT), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)

    script = r"""
from sqlalchemy.orm import configure_mappers
from sqlmodel import select

from core.celery_worker.celery_app import celery_app  # noqa: F401
import apps.export.tasks  # noqa: F401
from apps.export.models import DataExportRequest

configure_mappers()
compiled = str(select(DataExportRequest).compile())
assert "data_export_requests" in compiled.lower()
print("ok")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "name 'Invitation' is not defined" not in result.stderr
    assert "ok" in result.stdout
