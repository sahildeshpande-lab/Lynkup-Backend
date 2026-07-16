"""add_timestamps_to_post_reactions

Revision ID: 491715e37eb8
Revises: 97667d44497b
Create Date: 2026-07-10 15:19:44.446403

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '491715e37eb8'
down_revision: Union[str, Sequence[str], None] = '97667d44497b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add created_at and updated_at to post_reactions."""
    op.add_column(
        "post_reactions",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.add_column(
        "post_reactions",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    """Remove timestamp columns from post_reactions."""
    op.drop_column("post_reactions", "updated_at")
    op.drop_column("post_reactions", "created_at")
