"""add_bookmarks_and_share_events_tables

Revision ID: a1ec607ec695
Revises: 6e300747e3c4
Create Date: 2026-07-10 21:20:33.562420

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1ec607ec695'
down_revision: Union[str, Sequence[str], None] = '6e300747e3c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create bookmarks and share_events tables."""
    op.create_table(
        "bookmarks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("post_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "post_id", name="uq_bookmarks_user_post"),
    )
    op.create_index("ix_bookmarks_user_id", "bookmarks", ["user_id"], unique=False)
    op.create_index("ix_bookmarks_post_id", "bookmarks", ["post_id"], unique=False)
    op.create_index(op.f("ix_bookmarks_id"), "bookmarks", ["id"], unique=False)

    op.create_table(
        "share_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("post_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_share_events_user_id", "share_events", ["user_id"], unique=False)
    op.create_index("ix_share_events_post_id", "share_events", ["post_id"], unique=False)
    op.create_index(op.f("ix_share_events_id"), "share_events", ["id"], unique=False)


def downgrade() -> None:
    """Drop share_events and bookmarks tables."""
    op.drop_index(op.f("ix_share_events_id"), table_name="share_events")
    op.drop_index("ix_share_events_post_id", table_name="share_events")
    op.drop_index("ix_share_events_user_id", table_name="share_events")
    op.drop_table("share_events")

    op.drop_index(op.f("ix_bookmarks_id"), table_name="bookmarks")
    op.drop_index("ix_bookmarks_post_id", table_name="bookmarks")
    op.drop_index("ix_bookmarks_user_id", table_name="bookmarks")
    op.drop_table("bookmarks")
