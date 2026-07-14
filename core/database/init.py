from __future__ import annotations

from sqlmodel import SQLModel, select
from sqlalchemy import text

from .session import async_session_factory, engine

_db_initialized = False


async def init_db() -> None:
    global _db_initialized
    if _db_initialized:
        return
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(SQLModel.metadata.create_all)
    _db_initialized = True

    # # Isolated transactions for ALTER TYPE ADD VALUE
    # for val in ["draft", "processing", "published", "flagged", "hidden", "deleted"]:
    #     async with engine.begin() as conn:
    #         try:
    #             res = await conn.execute(text(
    #                 "SELECT 1 FROM pg_type t JOIN pg_enum e ON t.oid = e.enumtypid WHERE t.typname = 'poststate' AND e.enumlabel = :val"
    #             ), {"val": val})
    #             if not res.fetchone():
    #                 await conn.execute(text(f"ALTER TYPE poststate ADD VALUE '{val}'"))
    #         except Exception:
    #             pass
        
    #     async with engine.begin() as conn:
    #         try:
    #             res = await conn.execute(text(
    #                 "SELECT 1 FROM pg_type t JOIN pg_enum e ON t.oid = e.enumtypid WHERE t.typname = 'mediaassetstate' AND e.enumlabel = :val"
    #             ), {"val": val})
    #             if not res.fetchone():
    #                 await conn.execute(text(f"ALTER TYPE mediaassetstate ADD VALUE '{val}'"))
    #         except Exception:
    #             pass

    # async with engine.begin() as conn:
    #     try:
    #         await conn.execute(text("ALTER TYPE onboardingstatus ADD VALUE IF NOT EXISTS 'pending'"))
    #     except Exception:
    #         pass

    # async with engine.begin() as conn:
    #     await conn.execute(text("""
    #         CREATE TABLE IF NOT EXISTS profile_stats (
    #             id UUID PRIMARY KEY,
    #             profile_id UUID NOT NULL UNIQUE REFERENCES profiles(id),
    #             connection_count INTEGER NOT NULL DEFAULT 0
    #         )
    #     """))
    #     await conn.execute(text(
    #         "CREATE UNIQUE INDEX IF NOT EXISTS ix_profile_stats_profile_id ON profile_stats (profile_id)"
    #     ))
    #     await conn.execute(text("""
    #         CREATE TABLE IF NOT EXISTS moderation_words_config (
    #             id UUID PRIMARY KEY,
    #             profanity_words JSONB NOT NULL DEFAULT '[]',
    #             updated_at TIMESTAMP WITH TIME ZONE NOT NULL
    #         )
    #     """))
    #     await conn.execute(text(
    #         "ALTER TABLE moderation_words_config DROP COLUMN IF EXISTS spam_words"
    #     ))
    #     await conn.execute(text(
    #         "ALTER TABLE posts ADD COLUMN IF NOT EXISTS moderator_id UUID REFERENCES users(id)"
    #     ))
    #     await conn.execute(text(
    #         "ALTER TABLE posts ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMP WITH TIME ZONE"
    #     ))
    #     await conn.execute(text(
    #         "CREATE INDEX IF NOT EXISTS ix_posts_moderator_id ON posts (moderator_id)"
    #     ))
    #     await conn.execute(text("""
    #         CREATE TABLE IF NOT EXISTS moderation_assignment_state (
    #             id UUID PRIMARY KEY,
    #             last_assigned_moderator_id UUID REFERENCES users(id),
    #             updated_at TIMESTAMP WITH TIME ZONE NOT NULL
    #         )
    #     """))
    #     await conn.execute(text("""
    #         INSERT INTO moderation_assignment_state (id, last_assigned_moderator_id, updated_at)
    #         VALUES ('00000000-0000-0000-0000-000000000001'::uuid, NULL, NOW())
    #         ON CONFLICT (id) DO NOTHING
    #     """))

    # async with engine.begin() as conn:
    #     try:
    #         await conn.execute(text(
    #             "ALTER TABLE posts RENAME COLUMN is_admin_reviewed TO is_moderator_reviewed"
    #         ))
    #     except Exception:
    #         pass
    # _db_initialized = True
        # await conn.execute(text("ALTER TABLE profiles ADD COLUMN IF NOT EXISTS profile_photo_url VARCHAR(2048)"))
        # await conn.execute(text("ALTER TABLE profiles ADD COLUMN IF NOT EXISTS banner_photo_url VARCHAR(2048)"))
        # await conn.execute(text("ALTER TABLE profiles DROP COLUMN IF EXISTS profile_photo_media_id"))
        # await conn.execute(text("ALTER TABLE profiles DROP COLUMN IF EXISTS banner_media_id"))
        # await conn.execute(text("ALTER TABLE profiles ADD COLUMN IF NOT EXISTS welcome_message VARCHAR(255)"))
        # await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS registration_type VARCHAR(20) NOT NULL DEFAULT 'email'"))
        # await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255)"))
        # await conn.execute(text("ALTER TABLE users ALTER COLUMN firebase_uid DROP NOT NULL"))
        # await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN NOT NULL DEFAULT FALSE"))
        # await conn.execute(text("""
        #     INSERT INTO permissions (id, name, resource, action) VALUES
        #     (gen_random_uuid(), 'user', 'system', 'access'),
        #     (gen_random_uuid(), 'superadmin', 'system', 'access'),
        #     (gen_random_uuid(), 'moderator', 'system', 'access'),
        #     (gen_random_uuid(), 'viewer', 'system', 'access')
        #     ON CONFLICT (name) DO NOTHING
        # """))
