from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from core.database import models as _models  # noqa: F401
from core.database.config import settings

logger = logging.getLogger(__name__)


@dataclass
class AsyncWorkerRuntime:
    """
    Owns async resources used by a Celery worker child.

    The asyncio Runner, SQLAlchemy engine, and session factory all belong
    to this runtime and must be created and closed within the same worker
    process/asyncio loop.
    """

    runner: asyncio.Runner
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]

    def close(self) -> None:
        """Close the async resources owned by this worker runtime."""
        self.runner.run(self.engine.dispose())
        self.runner.close()


def _build_engine_kwargs() -> dict:
    """Build SQLAlchemy engine options from application settings."""
    engine_kwargs: dict = {}

    if os.getenv("DISABLE_DB_POOL", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        engine_kwargs["poolclass"] = NullPool

    elif settings.async_database_url.startswith("postgresql+asyncpg://"):
        engine_kwargs.update(
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout,
            pool_recycle=settings.db_pool_recycle,
        )

    return engine_kwargs


def create_worker_runtime() -> AsyncWorkerRuntime:
    """
    Create an async runtime for the current Celery worker child.

    This function must be called after the worker process has been created.
    It intentionally creates a new asyncio loop and a new SQLAlchemy async
    engine so loop-bound resources are not inherited from the parent process.
    """
    logger.info("Creating Celery task async runtime")

    from core.apns.client import reset_apns_client

    reset_apns_client()

    runner = asyncio.Runner()

    engine = create_async_engine(
        settings.async_database_url,
        connect_args=settings.async_connect_args,
        echo=settings.echo_sql,
        future=True,
        pool_pre_ping=True,
        **_build_engine_kwargs(),
    )

    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    runtime = AsyncWorkerRuntime(
        runner=runner,
        engine=engine,
        session_factory=session_factory,
    )

    logger.info("Celery task async runtime created")

    return runtime
