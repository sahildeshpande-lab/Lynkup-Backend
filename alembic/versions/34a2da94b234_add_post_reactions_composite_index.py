"""add_post_reactions_composite_index

Revision ID: 34a2da94b234
Revises: 55b045034816
Create Date: 2026-07-10 14:55:11.778140

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '34a2da94b234'
down_revision: Union[str, Sequence[str], None] = '55b045034816'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Ensure post_reactions lookup indexes exist."""
    op.create_index(
        "ix_post_reactions_post_id",
        "post_reactions",
        ["post_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_post_reactions_user_id",
        "post_reactions",
        ["user_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_post_reactions_post_id_user_id",
        "post_reactions",
        ["post_id", "user_id"],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    """Remove the composite lookup index from post_reactions."""
    op.drop_index("ix_post_reactions_post_id_user_id", table_name="post_reactions")
