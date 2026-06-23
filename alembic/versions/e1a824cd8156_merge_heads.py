"""merge heads

Revision ID: e1a824cd8156
Revises: b5f2c9d8a711, 9b5c1d7a4f2e
Create Date: 2026-06-23 10:55:35.975972

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1a824cd8156'
down_revision: Union[str, Sequence[str], None] = ('b5f2c9d8a711', '9b5c1d7a4f2e')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
