"""add_like_and_repost_counts_to_posts

Revision ID: 55b045034816
Revises: d49a0c9d2719
Create Date: 2026-07-10 14:49:07.238708

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '55b045034816'
down_revision: Union[str, Sequence[str], None] = 'd49a0c9d2719'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add like and repost counter columns to the ``posts`` table."""
    op.add_column(
        "posts",
        sa.Column("like_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "posts",
        sa.Column("repost_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    """Remove like and repost counter columns from the ``posts`` table."""
    op.drop_column("posts", "repost_count")
    op.drop_column("posts", "like_count")
