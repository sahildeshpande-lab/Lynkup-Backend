"""rename_is_admin_reviewed_to_is_moderator_reviewed

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-07-01 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("posts", "is_admin_reviewed", new_column_name="is_moderator_reviewed")


def downgrade() -> None:
    op.alter_column("posts", "is_moderator_reviewed", new_column_name="is_admin_reviewed")
