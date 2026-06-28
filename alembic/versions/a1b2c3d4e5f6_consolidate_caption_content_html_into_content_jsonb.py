"""consolidate caption and content_html into content jsonb

Revision ID: a1b2c3d4e5f6
Revises: 02acc265507a
Create Date: 2026-06-28 00:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '02acc265507a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Step 1: Add the new content JSONB column
    op.add_column('posts', sa.Column('content', JSONB(), nullable=True))

    # Step 2: Migrate existing data from caption and content_html into content JSONB
    op.execute(
        """
        UPDATE posts
        SET content = jsonb_build_object(
            'caption', caption,
            'content_html', content_html,
            'visibility', CASE
                WHEN state = 'hidden' THEN 'hidden'
                ELSE 'public'
            END
        )
        WHERE caption IS NOT NULL OR content_html IS NOT NULL
        """
    )

    # Set empty JSON for rows that had no data
    op.execute(
        """
        UPDATE posts
        SET content = '{}'::jsonb
        WHERE content IS NULL
        """
    )

    # Step 3: Drop the old columns
    op.drop_column('posts', 'caption')
    op.drop_column('posts', 'content_html')


def downgrade() -> None:
    """Downgrade schema."""
    # Step 1: Re-add the old columns
    op.add_column('posts', sa.Column('caption', sa.String(length=255), nullable=True))
    op.add_column('posts', sa.Column('content_html', sa.Text(), nullable=True))

    # Step 2: Migrate data back from content JSONB to separate columns
    op.execute(
        """
        UPDATE posts
        SET caption = content->>'caption',
            content_html = content->>'content_html'
        WHERE content IS NOT NULL
        """
    )

    # Step 3: Drop the content column
    op.drop_column('posts', 'content')
