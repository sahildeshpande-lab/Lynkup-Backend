"""add_profile_stats

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-01 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "profile_stats",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("connection_count", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("profile_id"),
    )
    op.create_index("ix_profile_stats_profile_id", "profile_stats", ["profile_id"], unique=True)

    op.execute(
        """
        INSERT INTO profile_stats (id, profile_id, connection_count)
        SELECT gen_random_uuid(), p.id,
            COALESCE((
                SELECT COUNT(*)::int
                FROM connections c
                WHERE c.is_active = true
                  AND (c.user_low_id = p.user_id OR c.user_high_id = p.user_id)
            ), 0)
        FROM profiles p
        """
    )


def downgrade() -> None:
    op.drop_index("ix_profile_stats_profile_id", table_name="profile_stats")
    op.drop_table("profile_stats")
