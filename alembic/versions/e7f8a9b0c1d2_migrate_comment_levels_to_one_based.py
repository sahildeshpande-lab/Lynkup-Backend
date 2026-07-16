"""migrate comment levels to one based indexing

Revision ID: e7f8a9b0c1d2
Revises: c4f8a1b2d3e5
Create Date: 2026-07-11 14:50:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7f8a9b0c1d2"
down_revision: Union[str, Sequence[str], None] = "c4f8a1b2d3e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE comments SET level = level + 1 WHERE level BETWEEN 0 AND 2")
    op.execute("UPDATE comments SET level = 3 WHERE level = 4")

    op.drop_constraint("ck_comments_level_range", "comments", type_="check")
    op.create_check_constraint(
        "ck_comments_level_range",
        "comments",
        "level >= 1 AND level <= 3",
    )
    op.alter_column("comments", "level", server_default="1")


def downgrade() -> None:
    op.execute("UPDATE comments SET level = level - 1 WHERE level BETWEEN 2 AND 3")
    op.execute("UPDATE comments SET level = 0 WHERE level = 1")

    op.drop_constraint("ck_comments_level_range", "comments", type_="check")
    op.create_check_constraint(
        "ck_comments_level_range",
        "comments",
        "level >= 0 AND level <= 3",
    )
    op.alter_column("comments", "level", server_default="0")
