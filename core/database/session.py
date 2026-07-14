from __future__ import annotations

import os
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from .config import settings
from . import models as _models  # noqa: F401

_engine_kwargs = {}
if os.getenv("DISABLE_DB_POOL", "").lower() in {"1", "true", "yes", "on"}:
    _engine_kwargs["poolclass"] = NullPool

engine = create_async_engine(
    settings.async_database_url,
    connect_args=settings.async_connect_args,
    echo=settings.echo_sql,
    future=True,
    pool_pre_ping=True,
    **_engine_kwargs,
)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session
