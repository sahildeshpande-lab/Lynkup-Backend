"""add_counts_to_profiles

Revision ID: c3d4e5f6a7b8
Revises: b2a3c4d5e6f7
Create Date: 2026-06-25 19:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "b2a3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    """Add integer count columns to the ``profiles`` table.
    All columns default to ``0`` and are non‑nullable.
    """
    op.add_column(
        "profiles",
        sa.Column("posts_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "profiles",
        sa.Column("followers_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "profiles",
        sa.Column("following_count", sa.Integer(), nullable=False, server_default="0"),
    )

def downgrade() -> None:
    """Remove the count columns from the ``profiles`` table."""
    op.drop_column("profiles", "following_count")
    op.drop_column("profiles", "followers_count")
    op.drop_column("profiles", "posts_count")
