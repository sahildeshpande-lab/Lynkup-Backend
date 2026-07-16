"""Update invitations code format to ABC1234.

Revision ID: 20260715_inv_code_fmt
Revises: 20260714_invitations
Create Date: 2026-07-15
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20260715_inv_code_fmt"
down_revision: Union[str, None] = "20260714_invitations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Safe for DBs that still have the digit-only constraint, and for fresh
    # installs whose create migration already uses ck_invitations_code_format.
    op.execute("ALTER TABLE invitations DROP CONSTRAINT IF EXISTS ck_invitations_code_seven_digits")
    op.execute("ALTER TABLE invitations DROP CONSTRAINT IF EXISTS ck_invitations_code_format")
    op.create_check_constraint(
        "ck_invitations_code_format",
        "invitations",
        "code ~ '^[A-Z]{3}[0-9]{4}$'",
    )


def downgrade() -> None:
    op.execute("ALTER TABLE invitations DROP CONSTRAINT IF EXISTS ck_invitations_code_format")
    op.create_check_constraint(
        "ck_invitations_code_seven_digits",
        "invitations",
        "code ~ '^[0-9]{7}$'",
    )
