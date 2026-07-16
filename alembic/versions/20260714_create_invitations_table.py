"""Add invitations table for invitation codes.

Revision ID: 20260714_invitations
Revises: 20260713_post_counts
Create Date: 2026-07-14
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260714_invitations"
down_revision: Union[str, None] = "20260713_post_counts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

invitationstatus = sa.Enum(
    "ACTIVE",
    "EXPIRED",
    "DEACTIVATED",
    name="invitationstatus",
    create_type=False,
)


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("referred_by_user_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_users_referred_by_user_id_users",
        "users",
        "users",
        ["referred_by_user_id"],
        ["id"],
    )
    op.create_index(
        "ix_users_referred_by_user_id",
        "users",
        ["referred_by_user_id"],
        unique=False,
    )

    invitationstatus.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "invitations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("inviter_user_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=7), nullable=False),
        sa.Column(
            "status",
            invitationstatus,
            nullable=False,
            server_default="ACTIVE",
        ),
        sa.Column(
            "redemption_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("redeemed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "is_converted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("deactivated_by", sa.Uuid(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "code ~ '^[A-Z]{3}[0-9]{4}$'",
            name="ck_invitations_code_format",
        ),
        sa.ForeignKeyConstraint(["inviter_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["redeemed_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["deactivated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_invitations_code", "invitations", ["code"], unique=True)
    op.create_index("ix_invitations_inviter_user_id", "invitations", ["inviter_user_id"], unique=False)
    op.create_index("ix_invitations_status", "invitations", ["status"], unique=False)
    op.create_index("ix_invitations_expires_at", "invitations", ["expires_at"], unique=False)
    op.create_index("ix_invitations_deleted_at", "invitations", ["deleted_at"], unique=False)
    op.create_index("ix_invitations_created_at", "invitations", ["created_at"], unique=False)
    op.create_index(
        "ix_invitations_inviter_created_at",
        "invitations",
        ["inviter_user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_invitations_redeemed_by_user_id",
        "invitations",
        ["redeemed_by_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_invitations_deactivated_by",
        "invitations",
        ["deactivated_by"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_invitations_deactivated_by", table_name="invitations")
    op.drop_index("ix_invitations_redeemed_by_user_id", table_name="invitations")
    op.drop_index("ix_invitations_inviter_created_at", table_name="invitations")
    op.drop_index("ix_invitations_created_at", table_name="invitations")
    op.drop_index("ix_invitations_deleted_at", table_name="invitations")
    op.drop_index("ix_invitations_expires_at", table_name="invitations")
    op.drop_index("ix_invitations_status", table_name="invitations")
    op.drop_index("ix_invitations_inviter_user_id", table_name="invitations")
    op.drop_index("ix_invitations_code", table_name="invitations")
    op.drop_table("invitations")
    invitationstatus.drop(op.get_bind(), checkfirst=True)

    op.drop_index("ix_users_referred_by_user_id", table_name="users")
    op.drop_constraint("fk_users_referred_by_user_id_users", "users", type_="foreignkey")
    op.drop_column("users", "referred_by_user_id")
