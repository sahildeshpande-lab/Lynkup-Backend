"""restructure_academic_interests

Revision ID: 6c881ff67552
Revises: c8b70dac94b8
Create Date: 2026-06-16 15:18:10.573206

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6c881ff67552'
down_revision: Union[str, Sequence[str], None] = 'c8b70dac94b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Drop old tables if they exist
    op.execute("DROP TABLE IF EXISTS profile_interests CASCADE")
    op.execute("DROP TABLE IF EXISTS academic_interest CASCADE")
    op.execute("DROP TABLE IF EXISTS academic_interests CASCADE")

    # Create new academic_interests table
    op.create_table(
        'academic_interests',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name')
    )

    # Update profiles table
    op.execute("ALTER TABLE profiles DROP CONSTRAINT IF EXISTS profiles_profile_interests_id_fkey")
    op.execute("DROP INDEX IF EXISTS ix_profiles_profile_interests_id")
    op.execute("ALTER TABLE profiles ALTER COLUMN profile_interests_id TYPE JSONB USING '[]'::jsonb")


def downgrade() -> None:
    """Downgrade schema."""
    # Revert profiles table alterations
    op.execute("ALTER TABLE profiles ALTER COLUMN profile_interests_id TYPE UUID USING NULL")
    op.create_index('ix_profiles_profile_interests_id', 'profiles', ['profile_interests_id'])
    
    # Re-create profile_interests table
    op.create_table('profile_interests',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('profile_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('interest_tag', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['profile_id'], ['profiles.id'], name='profile_interests_profile_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='profile_interests_pkey')
    )
    
    # Drop the new academic_interests table
    op.drop_table('academic_interests')
