"""replace content with content_html text and add caption to posts

Revision ID: 837cf7a942a6
Revises: c3d4e5f6a7b8
Create Date: 2026-06-26 13:31:47.481994

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '837cf7a942a6'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column('posts', 'content')
    op.add_column('posts', sa.Column('content_html', sa.Text(), nullable=True))
    op.add_column('posts', sa.Column('caption', sa.String(length=255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('posts', 'caption')
    op.drop_column('posts', 'content_html')
    op.add_column('posts', sa.Column('content', sa.JSON(), nullable=True))
