"""integer_academic_interest_ids

Revision ID: b5f2c9d8a711
Revises: ec1a02faae12
Create Date: 2026-06-17 23:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b5f2c9d8a711"
down_revision: Union[str, Sequence[str], None] = "ec1a02faae12"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Recreate academic_interests with integer IDs and remap profile JSON arrays."""
    op.execute("ALTER TABLE profiles DROP CONSTRAINT IF EXISTS profiles_profile_interests_id_fkey")
    op.execute("DROP INDEX IF EXISTS ix_profiles_profile_interests_id")

    op.execute("ALTER TABLE academic_interests RENAME TO academic_interests_old")
    op.create_table(
        "academic_interests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.execute(
        """
        INSERT INTO academic_interests (name, is_active, created_at, updated_at)
        SELECT name, is_active, created_at, updated_at
        FROM academic_interests_old
        ORDER BY lower(name), name, id::text
        """
    )

    op.execute(
        """
        UPDATE profiles
        SET profile_interests_id = COALESCE(
            (
                SELECT jsonb_agg(to_jsonb(resolved.interest_id) ORDER BY resolved.ordinality)
                FROM (
                    SELECT
                        elem.ordinality,
                        CASE
                            WHEN elem.value ~ '^[0-9]{1,10}$' THEN elem.value::int
                            ELSE new_interest.id
                        END AS interest_id
                    FROM jsonb_array_elements_text(
                        CASE
                            WHEN profile_interests_id IS NULL THEN '[]'::jsonb
                            WHEN jsonb_typeof(profile_interests_id::jsonb) = 'array' THEN profile_interests_id::jsonb
                            ELSE jsonb_build_array(profile_interests_id::jsonb)
                        END
                    )
                        WITH ORDINALITY AS elem(value, ordinality)
                    LEFT JOIN academic_interests_old old_interest
                        ON old_interest.id::text = elem.value
                    LEFT JOIN academic_interests new_interest
                        ON new_interest.name = old_interest.name
                ) AS resolved
                WHERE resolved.interest_id IS NOT NULL
            ),
            '[]'::jsonb
        )::json
        """
    )

    op.drop_table("academic_interests_old")


def downgrade() -> None:
    """Recreate academic_interests with deterministic UUID IDs."""
    op.execute("ALTER TABLE academic_interests RENAME TO academic_interests_int")
    op.create_table(
        "academic_interests",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.execute(
        """
        INSERT INTO academic_interests (id, name, is_active, created_at, updated_at)
        SELECT
            ('00000000-0000-0000-0000-' || lpad(id::text, 12, '0'))::uuid,
            name,
            is_active,
            created_at,
            updated_at
        FROM academic_interests_int
        ORDER BY id
        """
    )

    op.execute(
        """
        UPDATE profiles
        SET profile_interests_id = COALESCE(
            (
                SELECT jsonb_agg(to_jsonb(new_interest.id::text) ORDER BY elem.ordinality)
                FROM jsonb_array_elements_text(
                    CASE
                        WHEN profile_interests_id IS NULL THEN '[]'::jsonb
                        WHEN jsonb_typeof(profile_interests_id::jsonb) = 'array' THEN profile_interests_id::jsonb
                        ELSE jsonb_build_array(profile_interests_id::jsonb)
                    END
                )
                    WITH ORDINALITY AS elem(value, ordinality)
                JOIN academic_interests_int old_interest
                    ON old_interest.id::text = elem.value
                JOIN academic_interests new_interest
                    ON new_interest.name = old_interest.name
            ),
            '[]'::jsonb
        )::json
        """
    )

    op.drop_table("academic_interests_int")
