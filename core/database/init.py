from __future__ import annotations

from sqlmodel import SQLModel
from sqlalchemy import text

from .session import engine

_db_initialized = False


def _load_model_metadata() -> None:
    from core.database import models as _models  # noqa: F401


async def _ensure_post_state_enum() -> None:
    """Commit new PostgreSQL enum labels before startup queries can use them."""
    from common.enums import PostState

    async with engine.begin() as conn:
        for state in PostState:
            label = state.name.replace("'", "''")
            await conn.execute(text(
                f"""
                DO $$ BEGIN
                    ALTER TYPE poststate ADD VALUE IF NOT EXISTS '{label}';
                EXCEPTION
                    WHEN undefined_object THEN NULL;
                END $$
                """
            ))


async def init_db() -> None:
    global _db_initialized
    if _db_initialized:
        return
    _load_model_metadata()
    # create_all does not update enums on existing databases. New databases
    # create the complete type below; an absent type is safe to skip here.
    await _ensure_post_state_enum()
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(SQLModel.metadata.create_all)
        # Existing DBs: create_all does not add enum values or new columns.
        await conn.execute(
            text(
                """
                DO $$ BEGIN
                    ALTER TYPE invitationstatus ADD VALUE 'REDEEMED';
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
        # Celery auto-moderation claim/lease columns (posts + comments).
        await conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS posts
                ADD COLUMN IF NOT EXISTS moderation_attempt_count INTEGER NOT NULL DEFAULT 0
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS posts
                ADD COLUMN IF NOT EXISTS moderation_lease_owner VARCHAR(255)
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS posts
                ADD COLUMN IF NOT EXISTS moderation_lease_expires_at TIMESTAMPTZ
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS comments
                ADD COLUMN IF NOT EXISTS moderation_attempt_count INTEGER NOT NULL DEFAULT 0
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS comments
                ADD COLUMN IF NOT EXISTS moderation_lease_owner VARCHAR(255)
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS comments
                ADD COLUMN IF NOT EXISTS moderation_lease_expires_at TIMESTAMPTZ
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS reposts
                ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN NOT NULL DEFAULT false
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS reports
                ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN NOT NULL DEFAULT false
                """
            )
        )
        try:
            await conn.execute(
                text(
                    """
                    ALTER TABLE IF EXISTS profiles
                    ADD COLUMN IF NOT EXISTS connection_reminder_sent_at TIMESTAMPTZ
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    ALTER TABLE IF EXISTS profiles
                    ADD COLUMN IF NOT EXISTS graduation_completion_email_sent_at TIMESTAMPTZ
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    ALTER TABLE IF EXISTS profiles
                    ADD COLUMN IF NOT EXISTS is_alumni BOOLEAN
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    ALTER TABLE IF EXISTS users
                    ADD COLUMN IF NOT EXISTS has_changed_email_after_graduation BOOLEAN NOT NULL DEFAULT false
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS ix_connection_requests_receiver_user_id_status
                    ON connection_requests (receiver_user_id, status)
                    """
                )
            )
        except Exception:  # nosec B110 -- best-effort DB init
            pass
        try:
            await conn.execute(
                text(
                    """
                    ALTER TABLE IF EXISTS profiles
                    ALTER COLUMN is_alumni DROP NOT NULL
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    ALTER TABLE IF EXISTS profiles
                    ALTER COLUMN is_alumni DROP DEFAULT
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    UPDATE profiles
                    SET is_alumni = NULL
                    WHERE graduation_date IS NULL
                    """
                )
            )
        except Exception:  # nosec B110 -- best-effort DB init
            pass
        try:
            await _ensure_academic_catalog_schema(conn)
        except Exception:  # nosec B110 -- best-effort schema migration
            # Existing test/app DBs may not allow ALTER; Alembic migration is the source of truth.
            pass
        try:
            await _ensure_invitation_schema(conn)
        except Exception:  # nosec B110 -- best-effort schema migration
            pass
        try:
            await _ensure_share_event_branch_schema(conn)
        except Exception:  # nosec B110 -- best-effort schema migration
            pass
        try:
            await _ensure_notification_email_preferences_schema(conn)
        except Exception:  # nosec B110 -- best-effort schema migration
            pass
        try:
            await _ensure_user_posts_list_indexes(conn)
        except Exception:  # nosec B110 -- best-effort schema migration
            pass
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS profile_stats"))
    except Exception:  # nosec B110 -- existing DBs may lack DROP privilege
        pass
    _db_initialized = True


async def _ensure_user_posts_list_indexes(conn) -> None:
    """Composite indexes for GET /posts timeline (author state + active reposts).

    Uses IF NOT EXISTS (not CONCURRENTLY) because startup runs inside a
    transaction; CONCURRENTLY cannot run in a transaction block.
    """
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_posts_author_state_created
            ON posts (author_user_id, state, created_at DESC)
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_reposts_user_active_created
            ON reposts (user_id, created_at DESC)
            WHERE is_deleted = false
            """
        )
    )


async def _ensure_notification_email_preferences_schema(conn) -> None:
    """Add email_preferences JSONB on notification_preferences for existing DBs."""
    await conn.execute(
        text(
            """
            ALTER TABLE IF EXISTS notification_preferences
            ADD COLUMN IF NOT EXISTS email_preferences JSONB NOT NULL
            DEFAULT '{"bulk_email": true}'::jsonb
            """
        )
    )
    # Move weekly LynkUp reminder from email_preferences into category_preferences.
    await conn.execute(
        text(
            """
            UPDATE notification_preferences
            SET category_preferences =
                COALESCE(category_preferences, '{}'::jsonb)
                || jsonb_build_object(
                    'weekly_lynkup_request_reminder',
                    COALESCE(
                        (email_preferences ->> 'weekly_lynkup_request_reminder')::boolean,
                        true
                    )
                )
            WHERE email_preferences ? 'weekly_lynkup_request_reminder'
              AND NOT (COALESCE(category_preferences, '{}'::jsonb)
                       ? 'weekly_lynkup_request_reminder')
            """
        )
    )
    await conn.execute(
        text(
            """
            UPDATE notification_preferences
            SET email_preferences =
                ('{"bulk_email": true}'::jsonb || COALESCE(email_preferences, '{}'::jsonb))
                - 'weekly_lynkup_request_reminder'
            """
        )
    )


async def _ensure_invitation_schema(conn) -> None:
    """Widen invitations.code and drop the generated-format check on existing DBs."""
    from apps.invitations.config import INVITATION_CODE_MAX_LENGTH

    await conn.execute(
        text(
            f"""
            ALTER TABLE IF EXISTS invitations
            ALTER COLUMN code TYPE VARCHAR({INVITATION_CODE_MAX_LENGTH})
            """
        )
    )
    await conn.execute(
        text(
            """
            ALTER TABLE IF EXISTS invitations
            DROP CONSTRAINT IF EXISTS ck_invitations_code_format
            """
        )
    )


async def _ensure_share_event_branch_schema(conn) -> None:
    """Add Branch.io fields on share_events for existing databases (create_all will not)."""
    await conn.execute(
        text(
            """
            ALTER TABLE IF EXISTS share_events
            ADD COLUMN IF NOT EXISTS branch_code VARCHAR(64)
            """
        )
    )
    await conn.execute(
        text(
            """
            ALTER TABLE IF EXISTS share_events
            ADD COLUMN IF NOT EXISTS branch_url VARCHAR(2048)
            """
        )
    )
    await conn.execute(
        text(
            """
            DROP INDEX IF EXISTS ix_share_events_branch_code
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_share_events_branch_code
            ON share_events (branch_code)
            """
        )
    )


async def _ensure_academic_catalog_schema(conn) -> None:
    """Add additive catalog columns on existing databases (create_all will not)."""
    await conn.execute(
        text(
            """
            ALTER TABLE IF EXISTS profiles
            ADD COLUMN IF NOT EXISTS major_id INTEGER
            """
        )
    )
    await conn.execute(
        text(
            """
            ALTER TABLE IF EXISTS profiles
            ADD COLUMN IF NOT EXISTS minor_id INTEGER
            """
        )
    )
    await conn.execute(
        text(
            """
            ALTER TABLE IF EXISTS academic_interests
            ADD COLUMN IF NOT EXISTS major_id INTEGER
            """
        )
    )
    await conn.execute(
        text(
            """
            ALTER TABLE IF EXISTS academic_interests
            ADD COLUMN IF NOT EXISTS minor_id INTEGER
            """
        )
    )
    await conn.execute(
        text(
            """
            ALTER TABLE IF EXISTS countries
            ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true
            """
        )
    )
    await conn.execute(
        text(
            """
            ALTER TABLE IF EXISTS countries
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            """
        )
    )
    await conn.execute(
        text(
            """
            ALTER TABLE IF EXISTS countries
            ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            """
        )
    )
    await conn.execute(
        text(
            """
            ALTER TABLE IF EXISTS universities
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            """
        )
    )
    await conn.execute(
        text(
            """
            DO $$ BEGIN
                ALTER TABLE profiles
                    ADD CONSTRAINT fk_profiles_major_id
                    FOREIGN KEY (major_id) REFERENCES majors(id);
            EXCEPTION
                WHEN duplicate_object THEN NULL;
                WHEN undefined_table THEN NULL;
            END $$
            """
        )
    )
    await conn.execute(
        text(
            """
            DO $$ BEGIN
                ALTER TABLE profiles
                    ADD CONSTRAINT fk_profiles_minor_id
                    FOREIGN KEY (minor_id) REFERENCES minors(id);
            EXCEPTION
                WHEN duplicate_object THEN NULL;
                WHEN undefined_table THEN NULL;
            END $$
            """
        )
    )
    await conn.execute(
        text(
            """
            DO $$ BEGIN
                ALTER TABLE academic_interests
                    ADD CONSTRAINT fk_academic_interests_major_id
                    FOREIGN KEY (major_id) REFERENCES majors(id);
            EXCEPTION
                WHEN duplicate_object THEN NULL;
                WHEN undefined_table THEN NULL;
            END $$
            """
        )
    )
    await conn.execute(
        text(
            """
            DO $$ BEGIN
                ALTER TABLE academic_interests
                    ADD CONSTRAINT fk_academic_interests_minor_id
                    FOREIGN KEY (minor_id) REFERENCES minors(id);
            EXCEPTION
                WHEN duplicate_object THEN NULL;
                WHEN undefined_table THEN NULL;
            END $$
            """
        )
    )
    await conn.execute(
        text(
            """
            DO $$ BEGIN
                ALTER TABLE academic_interests
                    ADD CONSTRAINT ck_academic_interests_no_minor_only
                    CHECK ((major_id IS NOT NULL) OR (minor_id IS NULL));
            EXCEPTION
                WHEN duplicate_object THEN NULL;
            END $$
            """
        )
    )
    await conn.execute(
        text(
            """
            ALTER TABLE IF EXISTS academic_interests
            ADD COLUMN IF NOT EXISTS interest_added_by VARCHAR(16)
            """
        )
    )
    await conn.execute(
        text(
            """
            ALTER TABLE IF EXISTS majors
            ADD COLUMN IF NOT EXISTS major_added_by VARCHAR(16)
            """
        )
    )
    await conn.execute(
        text(
            """
            ALTER TABLE IF EXISTS minors
            ADD COLUMN IF NOT EXISTS minor_added_by VARCHAR(16)
            """
        )
    )
    await conn.execute(
        text(
            """
            UPDATE majors
            SET major_added_by = 'admin'
            WHERE major_added_by IS NULL
            """
        )
    )
    await conn.execute(
        text(
            """
            UPDATE minors
            SET minor_added_by = 'admin'
            WHERE minor_added_by IS NULL
            """
        )
    )
    await conn.execute(
        text(
            """
            DO $$ BEGIN
                ALTER TABLE academic_interests
                    ADD CONSTRAINT ck_academic_interests_added_by
                    CHECK (
                        interest_added_by IS NULL
                        OR interest_added_by IN ('user', 'admin')
                    );
            EXCEPTION
                WHEN duplicate_object THEN NULL;
            END $$
            """
        )
    )
    await conn.execute(
        text(
            """
            DO $$ BEGIN
                ALTER TABLE majors
                    ADD CONSTRAINT ck_majors_added_by
                    CHECK (
                        major_added_by IS NULL
                        OR major_added_by IN ('user', 'admin')
                    );
            EXCEPTION
                WHEN duplicate_object THEN NULL;
            END $$
            """
        )
    )
    await conn.execute(
        text(
            """
            DO $$ BEGIN
                ALTER TABLE minors
                    ADD CONSTRAINT ck_minors_added_by
                    CHECK (
                        minor_added_by IS NULL
                        OR minor_added_by IN ('user', 'admin')
                    );
            EXCEPTION
                WHEN duplicate_object THEN NULL;
            END $$
            """
        )
    )
    # Legacy unique-on-name (from when Column(name, unique=True)); blocks same
    # interest name under different major/minor combos. Prefer major-scoped indexes.
    await conn.execute(
        text(
            """
            ALTER TABLE IF EXISTS academic_interests
            DROP CONSTRAINT IF EXISTS academic_interests_name_key
            """
        )
    )
    await conn.execute(
        text(
            """
            ALTER TABLE IF EXISTS academic_interests
            DROP CONSTRAINT IF EXISTS academic_interests_name_key1
            """
        )
    )
    # Old education-level uniqueness blocks the same interest under different majors.
    await conn.execute(
        text(
            """
            ALTER TABLE IF EXISTS academic_interests
            DROP CONSTRAINT IF EXISTS uq_academic_interests_education_level_id_name
            """
        )
    )
    await conn.execute(
        text(
            """
            DROP INDEX IF EXISTS uq_academic_interests_education_level_id_name
            """
        )
    )
    # Major-scoped unique indexes must match SQLModel Index definitions
    # (column ``name``, not LOWER(name)) so Alembic autogen does not thrash.
    await conn.execute(
        text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_academic_interests_major_name_no_minor
            ON academic_interests (major_id, name)
            WHERE major_id IS NOT NULL AND minor_id IS NULL
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_academic_interests_major_minor_name
            ON academic_interests (major_id, minor_id, name)
            WHERE major_id IS NOT NULL AND minor_id IS NOT NULL
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_academic_interests_minor_id
            ON academic_interests (minor_id)
            """
        )
    )
