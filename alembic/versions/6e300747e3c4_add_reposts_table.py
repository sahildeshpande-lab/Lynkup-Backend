"""add_reposts_table

Revision ID: 6e300747e3c4
Revises: 491715e37eb8
Create Date: 2026-07-10 15:56:26.106647

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6e300747e3c4'
down_revision: Union[str, Sequence[str], None] = '491715e37eb8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create reposts table for tracking user reposts."""
    op.create_table(
        "reposts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("post_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"]),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("profile_id", "post_id", name="uq_reposts_profile_post"),
    )
    op.create_index("ix_reposts_profile_id", "reposts", ["profile_id"], unique=False)
    op.create_index("ix_reposts_post_id", "reposts", ["post_id"], unique=False)
    op.create_index("ix_reposts_profile_id_post_id", "reposts", ["profile_id", "post_id"], unique=False)
    op.create_index(op.f("ix_reposts_id"), "reposts", ["id"], unique=False)


def downgrade() -> None:
    """Drop reposts table."""
    op.drop_index(op.f("ix_reposts_id"), table_name="reposts")
    op.drop_index("ix_reposts_profile_id_post_id", table_name="reposts")
    op.drop_index("ix_reposts_post_id", table_name="reposts")
    op.drop_index("ix_reposts_profile_id", table_name="reposts")
    op.drop_table("reposts")
