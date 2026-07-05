from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import settings
from . import models as _models  # noqa: F401

engine = create_async_engine(
    settings.async_database_url,
    echo=settings.echo_sql,
    future=True,
    pool_pre_ping=True,
    connect_args={
        "ssl": ssl.create_default_context()
    },
)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session
