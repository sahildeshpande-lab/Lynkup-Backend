"""update media assets columns

Revision ID: 02acc265507a
Revises: 837cf7a942a6
Create Date: 2026-06-26 13:50:23.661353

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '02acc265507a'
down_revision: Union[str, Sequence[str], None] = '837cf7a942a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('media_assets', sa.Column('key', sa.String(length=255), nullable=False))
    op.add_column('media_assets', sa.Column('type', sa.String(length=20), nullable=False))
    op.add_column('media_assets', sa.Column('original_filename', sa.String(length=255), nullable=True))
    op.add_column('media_assets', sa.Column('mime_type', sa.String(length=100), nullable=True))
    op.add_column('media_assets', sa.Column('file_size', sa.Integer(), nullable=True))
    op.add_column('media_assets', sa.Column('state', sa.String(length=20), nullable=False))
    
    op.drop_index('ix_media_assets_status', table_name='media_assets')
    op.create_index(op.f('ix_media_assets_type'), 'media_assets', ['type'], unique=False)
    op.create_unique_constraint('uq_media_assets_key', 'media_assets', ['key'])
    op.drop_column('media_assets', 'status')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('media_assets', sa.Column('status', sa.String(length=20), nullable=False))
    op.drop_constraint('uq_media_assets_key', 'media_assets', type_='unique')
    op.drop_index(op.f('ix_media_assets_type'), table_name='media_assets')
    op.create_index('ix_media_assets_status', 'media_assets', ['status'], unique=False)
    
    op.drop_column('media_assets', 'state')
    op.drop_column('media_assets', 'file_size')
    op.drop_column('media_assets', 'mime_type')
    op.drop_column('media_assets', 'original_filename')
    op.drop_column('media_assets', 'type')
    op.drop_column('media_assets', 'key')
