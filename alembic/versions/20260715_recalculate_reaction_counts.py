"""Recalculate reaction counts for posts and comments.

Revision ID: 20260715_recalc_reactions
Revises: 20260715_inv_code_fmt
Create Date: 2026-07-15
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20260715_recalc_reactions"
down_revision: Union[str, None] = "20260715_inv_code_fmt"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE posts
        SET like_count = (
            SELECT COALESCE(COUNT(*), 0)
            FROM post_reactions
            WHERE post_reactions.post_id = posts.id
        )
        """
    )
    op.execute(
        """
        UPDATE comments
        SET like_count = (
            SELECT COALESCE(COUNT(*), 0)
            FROM comment_reactions
            WHERE comment_reactions.comment_id = comments.id
        )
        """
    )


def downgrade() -> None:
    # Downgrade reverts to only counting "like" reaction types.
    op.execute(
        """
        UPDATE posts
        SET like_count = (
            SELECT COALESCE(COUNT(*), 0)
            FROM post_reactions
            WHERE post_reactions.post_id = posts.id
              AND post_reactions.reaction_type = 'like'
        )
        """
    )
    op.execute(
        """
        UPDATE comments
        SET like_count = (
            SELECT COALESCE(COUNT(*), 0)
            FROM comment_reactions
            WHERE comment_reactions.comment_id = comments.id
              AND comment_reactions.reaction_type = 'like'
        )
        """
    )
