"""add user_id to reposts and add uq_reposts_user_post

Revision ID: 20260716_add_user_id_to_reposts
Revises: 20260715_recalc_reactions
Create Date: 2026-07-16
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260716_add_user_id_to_reposts"
down_revision: Union[str, None] = "20260715_recalc_reactions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add user_id column as nullable initially
    op.add_column("reposts", sa.Column("user_id", sa.Uuid(), nullable=True))

    # Populate user_id by joining with profiles table
    op.execute(
        """
        UPDATE reposts
        SET user_id = profiles.user_id
        FROM profiles
        WHERE reposts.profile_id = profiles.id
        """
    )

    # Alter column to be not null
    op.alter_column("reposts", "user_id", nullable=False)

    # Add foreign key constraint
    op.create_foreign_key(
        "fk_reposts_user_id_users",
        "reposts",
        "users",
        ["user_id"],
        ["id"],
    )

    # Add index on user_id
    op.create_index("ix_reposts_user_id", "reposts", ["user_id"], unique=False)

    # Add unique constraint on (user_id, post_id)
    op.create_unique_constraint("uq_reposts_user_post", "reposts", ["user_id", "post_id"])


def downgrade() -> None:
    op.drop_constraint("uq_reposts_user_post", "reposts", type_="unique")
    op.drop_index("ix_reposts_user_id", table_name="reposts")
    op.drop_constraint("fk_reposts_user_id_users", "reposts", type_="foreignkey")
    op.drop_column("reposts", "user_id")
