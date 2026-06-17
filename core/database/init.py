from __future__ import annotations

from sqlmodel import SQLModel, select
from sqlalchemy import text

from .session import async_session_factory, engine




async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(SQLModel.metadata.create_all)
        try:
            await conn.execute(text("ALTER TYPE onboardingstatus ADD VALUE IF NOT EXISTS 'pending'"))
        except Exception:
            pass
        await conn.execute(text("ALTER TABLE refresh_tokens ALTER COLUMN expires_at DROP NOT NULL"))

        await conn.execute(text("ALTER TABLE transactional_email_log ADD COLUMN IF NOT EXISTS subject VARCHAR(256) NOT NULL DEFAULT ''"))
        await conn.execute(text("ALTER TABLE transactional_email_log ADD COLUMN IF NOT EXISTS is_sent BOOLEAN NOT NULL DEFAULT FALSE"))
        await conn.execute(text("ALTER TABLE profiles ADD COLUMN IF NOT EXISTS profile_photo_url VARCHAR(2048)"))
        await conn.execute(text("ALTER TABLE profiles ADD COLUMN IF NOT EXISTS banner_photo_url VARCHAR(2048)"))
        await conn.execute(text("ALTER TABLE profiles DROP COLUMN IF EXISTS profile_photo_media_id"))
        await conn.execute(text("ALTER TABLE profiles DROP COLUMN IF EXISTS banner_media_id"))
        await conn.execute(text("ALTER TABLE profiles ADD COLUMN IF NOT EXISTS welcome_message VARCHAR(255)"))
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS registration_type VARCHAR(20) NOT NULL DEFAULT 'email'"))
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255)"))
        await conn.execute(text("ALTER TABLE users ALTER COLUMN firebase_uid DROP NOT NULL"))
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN NOT NULL DEFAULT FALSE"))
        
        # Academic program table removal and field additions/removals
        await conn.execute(text("DROP TABLE IF EXISTS academic_programs CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS user_identities CASCADE"))
        await conn.execute(text("ALTER TABLE profiles DROP COLUMN IF EXISTS academic_program_id"))
        await conn.execute(text("ALTER TABLE profiles ADD COLUMN IF NOT EXISTS profile_interests_id UUID"))
        await conn.execute(text("ALTER TABLE universities ADD COLUMN IF NOT EXISTS academic_program JSON"))

        # Schema migrations for users table
        res_email = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='primary_email_normalized'"
        ))
        if res_email.fetchone() is not None:
            await conn.execute(text("ALTER TABLE users RENAME COLUMN primary_email_normalized TO email"))

        await conn.execute(text("ALTER TABLE users DROP COLUMN IF EXISTS is_send"))
        await conn.execute(text("ALTER TABLE users DROP COLUMN IF EXISTS username"))

        # Check if 'role' column exists in 'users' to migrate roles to the new 'roles' and 'user_roles' tables
        res = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='role'"
        ))
        if res.fetchone() is not None:
            # Migrate distinct roles
            await conn.execute(text("""
                INSERT INTO roles (id, name, description, created_at)
                SELECT gen_random_uuid(), r, r || ' role', NOW()
                FROM (SELECT DISTINCT role FROM users WHERE role IS NOT NULL) AS temp_roles(r)
                ON CONFLICT (name) DO NOTHING
            """))
            # Associate users with their roles
            await conn.execute(text("""
                INSERT INTO user_roles (id, user_id, role_id, assigned_at)
                SELECT gen_random_uuid(), users.id, roles.id, NOW()
                FROM users
                JOIN roles ON roles.name = users.role
                ON CONFLICT DO NOTHING
            """))
            # Drop the obsolete role column
            await conn.execute(text("ALTER TABLE users DROP COLUMN IF EXISTS role"))

        # Seed fixed roles: "user", "superadmin", "moderator", "viewer"
        await conn.execute(text("""
            INSERT INTO roles (id, name, description, created_at) VALUES
            (gen_random_uuid(), 'user', 'User role', NOW()),
            (gen_random_uuid(), 'superadmin', 'Superadmin role', NOW()),
            (gen_random_uuid(), 'moderator', 'Moderator role', NOW()),
            (gen_random_uuid(), 'viewer', 'Viewer role', NOW())
            ON CONFLICT (name) DO NOTHING
        """))

        # Seed fixed permissions with name: "user", "superadmin", "moderator", "viewer"
        await conn.execute(text("""
            INSERT INTO permissions (id, name, resource, action) VALUES
            (gen_random_uuid(), 'user', 'system', 'access'),
            (gen_random_uuid(), 'superadmin', 'system', 'access'),
            (gen_random_uuid(), 'moderator', 'system', 'access'),
            (gen_random_uuid(), 'viewer', 'system', 'access')
            ON CONFLICT (name) DO NOTHING
        """))

