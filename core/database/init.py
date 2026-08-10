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
        # Existing DBs: create_all does not add enum values or new columns.
        await conn.execute(
            text(
                """
                DO $$ BEGIN
                    ALTER TYPE poststate ADD VALUE 'escalate';
                EXCEPTION
                    WHEN duplicate_object THEN NULL;
                    WHEN undefined_object THEN NULL;
                END $$
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS posts
                ADD COLUMN IF NOT EXISTS moderation_notes TEXT
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS posts
                ADD COLUMN IF NOT EXISTS auto_moderation_scanned_at TIMESTAMPTZ
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS posts
                ADD COLUMN IF NOT EXISTS moderation_words_found JSONB
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS comments
                ADD COLUMN IF NOT EXISTS auto_moderation_scanned_at TIMESTAMPTZ
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS comments
                ADD COLUMN IF NOT EXISTS moderation_words_found JSONB
                """
            )
        )
    _db_initialized = True
