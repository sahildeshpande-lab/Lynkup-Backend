"""add_moderator_assignment_fields

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-07-01 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, Sequence[str], None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SINGLETON_STATE_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    op.add_column("posts", sa.Column("moderator_id", sa.Uuid(), nullable=True))
    op.add_column("posts", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_posts_moderator_id_users",
        "posts",
        "users",
        ["moderator_id"],
        ["id"],
    )
    op.create_index("ix_posts_moderator_id", "posts", ["moderator_id"], unique=False)

    op.create_table(
        "moderation_assignment_state",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("last_assigned_moderator_id", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["last_assigned_moderator_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.execute(
        sa.text(
            f"""
            INSERT INTO moderation_assignment_state (id, last_assigned_moderator_id, updated_at)
            VALUES ('{SINGLETON_STATE_ID}'::uuid, NULL, NOW())
            ON CONFLICT (id) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.drop_table("moderation_assignment_state")
    op.drop_index("ix_posts_moderator_id", table_name="posts")
    op.drop_constraint("fk_posts_moderator_id_users", "posts", type_="foreignkey")
    op.drop_column("posts", "reviewed_at")
    op.drop_column("posts", "moderator_id")
