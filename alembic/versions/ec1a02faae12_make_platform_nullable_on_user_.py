"""make_platform_nullable_on_user_installations

Revision ID: ec1a02faae12
Revises: f7efa0632285
Create Date: 2026-06-17 14:55:39.564867

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ec1a02faae12'
down_revision: Union[str, Sequence[str], None] = 'f7efa0632285'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Make platform column nullable
    op.alter_column('user_installations', 'platform', existing_type=sa.String(32), nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    # Revert platform column to NOT NULL
    op.alter_column('user_installations', 'platform', existing_type=sa.String(32), nullable=False)
