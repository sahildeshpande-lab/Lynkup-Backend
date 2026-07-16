"""convert_post_reactions_reaction_type_to_enum

Revision ID: 97667d44497b
Revises: 34a2da94b234
Create Date: 2026-07-10 15:11:02.817799

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '97667d44497b'
down_revision: Union[str, Sequence[str], None] = '34a2da94b234'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Convert post_reactions.reaction_type from VARCHAR to PostgreSQL enum."""
    reactiontype = sa.Enum(
        "like",
        "celebrate",
        "insightful",
        "support",
        "curious",
        name="reactiontype",
    )
    reactiontype.create(op.get_bind(), checkfirst=True)

    op.execute(
        sa.text(
            "UPDATE post_reactions SET reaction_type = 'like' "
            "WHERE reaction_type = 'love'"
        )
    )

    op.alter_column(
        "post_reactions",
        "reaction_type",
        existing_type=sa.String(length=20),
        type_=reactiontype,
        existing_nullable=False,
        postgresql_using="reaction_type::reactiontype",
    )


def downgrade() -> None:
    """Revert post_reactions.reaction_type to VARCHAR."""
    reactiontype = sa.Enum(
        "like",
        "celebrate",
        "insightful",
        "support",
        "curious",
        name="reactiontype",
    )

    op.alter_column(
        "post_reactions",
        "reaction_type",
        existing_type=reactiontype,
        type_=sa.String(length=20),
        existing_nullable=False,
        postgresql_using="reaction_type::text",
    )
    reactiontype.drop(op.get_bind(), checkfirst=True)
