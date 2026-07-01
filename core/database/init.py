from __future__ import annotations

from sqlmodel import SQLModel, select
from sqlalchemy import text

from .session import async_session_factory, engine




async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        # await conn.run_sync(SQLModel.metadata.create_all)

    # Isolated transactions for ALTER TYPE ADD VALUE
    for val in ["draft", "processing", "published", "flagged", "hidden", "deleted"]:
        async with engine.begin() as conn:
            try:
                res = await conn.execute(text(
                    "SELECT 1 FROM pg_type t JOIN pg_enum e ON t.oid = e.enumtypid WHERE t.typname = 'poststate' AND e.enumlabel = :val"
                ), {"val": val})
                if not res.fetchone():
                    await conn.execute(text(f"ALTER TYPE poststate ADD VALUE '{val}'"))
            except Exception:
                pass
        
        async with engine.begin() as conn:
            try:
                res = await conn.execute(text(
                    "SELECT 1 FROM pg_type t JOIN pg_enum e ON t.oid = e.enumtypid WHERE t.typname = 'mediaassetstate' AND e.enumlabel = :val"
                ), {"val": val})
                if not res.fetchone():
                    await conn.execute(text(f"ALTER TYPE mediaassetstate ADD VALUE '{val}'"))
            except Exception:
                pass

    async with engine.begin() as conn:
        try:
            await conn.execute(text("ALTER TYPE onboardingstatus ADD VALUE IF NOT EXISTS 'pending'"))
        except Exception:
            pass

        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS profile_stats (
                id UUID PRIMARY KEY,
                profile_id UUID NOT NULL UNIQUE REFERENCES profiles(id),
                connection_count INTEGER NOT NULL DEFAULT 0
            )
        """))
        await conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_profile_stats_profile_id ON profile_stats (profile_id)"
        ))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS moderation_words_config (
                id UUID PRIMARY KEY,
                spam_words JSONB NOT NULL DEFAULT '[]',
                profanity_words JSONB NOT NULL DEFAULT '[]',
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL
            )
        """))
        await conn.execute(text(
            "ALTER TABLE posts ADD COLUMN IF NOT EXISTS moderator_id UUID REFERENCES users(id)"
        ))
        await conn.execute(text(
            "ALTER TABLE posts ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMP WITH TIME ZONE"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_posts_moderator_id ON posts (moderator_id)"
        ))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS moderation_assignment_state (
                id UUID PRIMARY KEY,
                last_assigned_moderator_id UUID REFERENCES users(id),
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL
            )
        """))
        await conn.execute(text("""
            INSERT INTO moderation_assignment_state (id, last_assigned_moderator_id, updated_at)
            VALUES ('00000000-0000-0000-0000-000000000001'::uuid, NULL, NOW())
            ON CONFLICT (id) DO NOTHING
        """))
        try:
            await conn.execute(text(
                "ALTER TABLE posts RENAME COLUMN is_admin_reviewed TO is_moderator_reviewed"
            ))
        except Exception:
            pass
        # await conn.execute(text("ALTER TABLE transactional_email_log ADD COLUMN IF NOT EXISTS is_sent BOOLEAN NOT NULL DEFAULT FALSE"))
        # await conn.execute(text("ALTER TABLE transactional_email_log ADD COLUMN IF NOT EXISTS sent_at TIMESTAMP WITH TIME ZONE"))
        # await conn.execute(text("ALTER TABLE transactional_email_log ADD COLUMN IF NOT EXISTS error_message TEXT"))
        # await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_transactional_email_log_is_sent_created_at ON transactional_email_log (is_sent, created_at)"))
        # await conn.execute(text("ALTER TABLE profiles ADD COLUMN IF NOT EXISTS profile_photo_url VARCHAR(2048)"))
        # await conn.execute(text("ALTER TABLE profiles ADD COLUMN IF NOT EXISTS banner_photo_url VARCHAR(2048)"))
        # await conn.execute(text("ALTER TABLE profiles DROP COLUMN IF EXISTS profile_photo_media_id"))
        # await conn.execute(text("ALTER TABLE profiles DROP COLUMN IF EXISTS banner_media_id"))
        # await conn.execute(text("ALTER TABLE profiles ADD COLUMN IF NOT EXISTS welcome_message VARCHAR(255)"))
        # await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS registration_type VARCHAR(20) NOT NULL DEFAULT 'email'"))
        # await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255)"))
        # await conn.execute(text("ALTER TABLE users ALTER COLUMN firebase_uid DROP NOT NULL"))
        # await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN NOT NULL DEFAULT FALSE"))
        # # await conn.execute(text("ALTER TABLE pawait conn.execute(text("ALTER TABLE refresh_tokens ALTER COLUMN expires_at DROP NOT NULL"))osts ADD COLUMN IF NOT EXISTS is_admin_reviewed BOOLEAN NOT NULL DEFAULT FALSE"))
        
        # # Academic program table removal and field additions/removals
        # await conn.execute(text("DROP TABLE IF EXISTS academic_programs CASCADE"))
        # await conn.execute(text("DROP TABLE IF EXISTS user_identities CASCADE"))
        # await conn.execute(text("ALTER TABLE profiles ADD COLUMN IF NOT EXISTS profile_interests_id UUID"))
        # await conn.execute(text("ALTER TABLE universities ADD COLUMN IF NOT EXISTS academic_program JSON"))

        # # Schema migrations for users table
        # res_email = await conn.execute(text(
        #     "SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='primary_email_normalized'"
        # ))
        # if res_email.fetchone() is not None:
        #     await conn.execute(text("ALTER TABLE users RENAME COLUMN primary_email_normalized TO email"))

        # await conn.execute(text("ALTER TABLE users DROP COLUMN IF EXISTS is_send"))
        # await conn.execute(text("ALTER TABLE users DROP COLUMN IF EXISTS username"))

        # # Check if 'role' column exists in 'users' to migrate roles to the new 'roles' and 'user_roles' tables
        # res = await conn.execute(text(
        #     "SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='role'"
        # ))
        # if res.fetchone() is not None:
        #     # Migrate distinct roles
        #     await conn.execute(text("""
        #         INSERT INTO roles (id, name, description, created_at)
        #         SELECT gen_random_uuid(), r, r || ' role', NOW()
        #         FROM (SELECT DISTINCT role FROM users WHERE role IS NOT NULL) AS temp_roles(r)
        #         ON CONFLICT (name) DO NOTHING
        #     """))
        #     # Associate users with their roles
        #     await conn.execute(text("""
        #         INSERT INTO user_roles (id, user_id, role_id, assigned_at)
        #         SELECT gen_random_uuid(), users.id, roles.id, NOW()
        #         FROM users
        #         JOIN roles ON roles.name = users.role
        #         ON CONFLICT DO NOTHING
        #     """))
        #     # Drop the obsolete role column
        #     await conn.execute(text("ALTER TABLE users DROP COLUMN IF EXISTS role"))

        # Seed fixed roles: "user", "superadmin", "moderator", "viewer"
        # await conn.execute(text("""
        #     INSERT INTO roles (id, name, description, created_at) VALUES
        #     (gen_random_uuid(), 'user', 'User role', NOW()),
        #     (gen_random_uuid(), 'superadmin', 'Superadmin role', NOW()),
        #     (gen_random_uuid(), 'moderator', 'Moderator role', NOW()),
        #     (gen_random_uuid(), 'viewer', 'Viewer role', NOW())
        #     ON CONFLICT (name) DO NOTHING
        # """))

        # Seed fixed permissions with name: "user", "superadmin", "moderator", "viewer"
        # await conn.execute(text("""
        #     INSERT INTO permissions (id, name, resource, action) VALUES
        #     (gen_random_uuid(), 'user', 'system', 'access'),
        #     (gen_random_uuid(), 'superadmin', 'system', 'access'),
        #     (gen_random_uuid(), 'moderator', 'system', 'access'),
        #     (gen_random_uuid(), 'viewer', 'system', 'access')
        #     ON CONFLICT (name) DO NOTHING
        # """))

