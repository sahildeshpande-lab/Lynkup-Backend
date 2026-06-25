"""remove_welcome_message_from_profiles

Revision ID: b2a3c4d5e6f7
Revises: 9e17c2e42516
Create Date: 2026-06-25 13:30:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "b2a3c4d5e6f7"
down_revision = "9e17c2e42516"
branch_labels = None
depends_on = None

def upgrade() -> None:
    """Drop the welcome_message column from profiles table."""
    op.drop_column("profiles", "welcome_message")

def downgrade() -> None:
    """Re‑add the welcome_message column (nullable, length 255)."""
    op.add_column(
        "profiles",
        sa.Column("welcome_message", sa.String(length=255), nullable=True),
    )
