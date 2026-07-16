from __future__ import annotations

from sqlmodel import SQLModel
from sqlalchemy import text

from .session import engine

_db_initialized = False


def _load_model_metadata() -> None:
    from core.database import models as _models  # noqa: F401


async def init_db() -> None:
    global _db_initialized
    if _db_initialized:
        return
    _load_model_metadata()
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(SQLModel.metadata.create_all)
    _db_initialized = True
