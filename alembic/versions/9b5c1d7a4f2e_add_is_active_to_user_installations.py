"""add_is_active_to_user_installations

Revision ID: 9b5c1d7a4f2e
Revises: ec1a02faae12
Create Date: 2026-06-22 19:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9b5c1d7a4f2e"
down_revision: Union[str, Sequence[str], None] = "ec1a02faae12"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "user_installations",
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("user_installations", "is_active")
