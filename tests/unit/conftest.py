from __future__ import annotations

import os

# Import all db models to populate SQLModel registry and avoid mapping errors in unit tests
from apps.accounts.db_models import *
from apps.administration.db_models import *  # noqa: F401
from apps.profiles.db_models import *
from apps.feed.db_models import *
from apps.invitations.db_models import *
from apps.engagement.db_models import *
from apps.connections.db_models import *
from apps.analytics.db_models import *  # noqa: F401
from apps.export.models import *  # noqa: F401
from apps.bulk_send.models import *  # noqa: F401
from apps.learningspotlight.db_models import *  # noqa: F401

import pytest
from unittest.mock import AsyncMock, Mock

os.environ.setdefault("DISABLE_DB_POOL", "true")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("DATABASE_TEST_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("CELERY_BROKER_URL", "redis://localhost:6379/0")
os.environ.setdefault("SMTP_HOST", "smtp.example.test")
os.environ.setdefault("SMTP_PORT", "587")
os.environ.setdefault("SMTP_USERNAME", "unit@example.test")
os.environ.setdefault("SMTP_PASSWORD", "secret")
os.environ.setdefault("SMTP_FROM_EMAIL", "noreply@example.test")

from datetime import datetime, timezone

from sqlalchemy import event
from sqlalchemy.engine import Engine


@event.listens_for(Engine, "connect")
def _register_sqlite_now(dbapi_connection, _connection_record) -> None:
    """SQLite has no now(); Country timestamps use PostgreSQL now() as the source of truth."""
    create_function = getattr(dbapi_connection, "create_function", None)
    if create_function is None:
        return
    try:
        create_function(
            "now",
            0,
            lambda: datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ", timespec="microseconds"),
        )
    except (AttributeError, ValueError, TypeError):
        pass


class FakeScalarResult:
    def __init__(self, value=None, values=None):
        self.value = value
        self.values = [] if values is None else values

    def scalar_one(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.values

    def first(self):
        return self.values[0] if self.values else None


@pytest.fixture
def scalar_result():
    return FakeScalarResult


@pytest.fixture
def mock_db():
    def build(*results):
        queue = list(results)

        async def execute(_statement, *args, **kwargs):
            if queue:
                return queue.pop(0)
            return FakeScalarResult()

        db = Mock()
        db.execute = AsyncMock(side_effect=execute)
        db.commit = AsyncMock()
        db.refresh = AsyncMock()
        db.flush = AsyncMock()
        db.rollback = AsyncMock()
        db.add = Mock()
        db.delete = AsyncMock()
        return db

    return build

