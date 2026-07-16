"""Add share_count and comment_count to posts; backfill and dedupe share_events.

Revision ID: 20260713_post_counts
Revises:
Create Date: 2026-07-13
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260713_post_counts"
down_revision: Union[str, None] = "e7f8a9b0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "posts",
        sa.Column("share_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "posts",
        sa.Column("comment_count", sa.Integer(), nullable=False, server_default="0"),
    )

    op.execute(
        """
        UPDATE posts AS p
        SET share_count = sub.cnt
        FROM (
            SELECT post_id, COUNT(*) AS cnt
            FROM share_events
            GROUP BY post_id
        ) AS sub
        WHERE p.id = sub.post_id
        """
    )

    op.execute(
        """
        UPDATE posts AS p
        SET comment_count = sub.cnt
        FROM (
            SELECT post_id, COUNT(*) AS cnt
            FROM comments
            WHERE parent_comment_id IS NULL
            GROUP BY post_id
        ) AS sub
        WHERE p.id = sub.post_id
        """
    )

    op.execute(
        """
        DELETE FROM share_events AS se
        USING share_events AS se2
        WHERE se.user_id = se2.user_id
          AND se.post_id = se2.post_id
          AND se.created_at > se2.created_at
        """
    )

    op.create_unique_constraint(
        "uq_share_events_user_post",
        "share_events",
        ["user_id", "post_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_share_events_user_post", "share_events", type_="unique")
    op.drop_column("posts", "comment_count")
    op.drop_column("posts", "share_count")
