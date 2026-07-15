from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, Enum, Index, Integer, String
from sqlalchemy.orm import relationship
from sqlmodel import Field, Relationship, SQLModel

from common.enums import InvitationStatus
from common.time import utc_now

if TYPE_CHECKING:
    from apps.accounts.db_models import User


class Invitation(SQLModel, table=True):
    __tablename__ = "invitations"

    id: UUID = Field(default_factory=uuid4, primary_key=True)

    inviter_user_id: UUID = Field(foreign_key="users.id", nullable=False)
    code: str = Field(
        sa_column=Column(String(7), nullable=False),
    )
    status: InvitationStatus = Field(
        default=InvitationStatus.active,
        sa_column=Column(
            Enum(
                InvitationStatus,
                name="invitationstatus",
                values_callable=lambda enum_cls: [member.value for member in enum_cls],
            ),
            nullable=False,
        ),
    )

    redemption_count: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default="0"),
    )
    redeemed_by_user_id: UUID | None = Field(
        default=None,
        foreign_key="users.id",
        nullable=True,
    )
    redeemed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    expires_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    is_active: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default="true"),
    )
    is_converted: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default="false"),
    )

    deactivated_by: UUID | None = Field(
        default=None,
        foreign_key="users.id",
        nullable=True,
    )
    deleted_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )

    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    inviter: Optional["User"] = Relationship(
        sa_relationship=relationship(
            "User",
            back_populates="invitations_created",
            foreign_keys="[Invitation.inviter_user_id]",
        )
    )
    redeemed_by: Optional["User"] = Relationship(
        sa_relationship=relationship(
            "User",
            back_populates="invitations_redeemed",
            foreign_keys="[Invitation.redeemed_by_user_id]",
        )
    )
    deactivator: Optional["User"] = Relationship(
        sa_relationship=relationship(
            "User",
            back_populates="invitations_deactivated",
            foreign_keys="[Invitation.deactivated_by]",
        )
    )

    __table_args__ = (
        CheckConstraint(
            "code ~ '^[A-Z]{3}[0-9]{4}$'",
            name="ck_invitations_code_format",
        ),
        Index("ix_invitations_code", "code", unique=True),
        Index("ix_invitations_inviter_user_id", "inviter_user_id"),
        Index("ix_invitations_status", "status"),
        Index("ix_invitations_expires_at", "expires_at"),
        Index("ix_invitations_deleted_at", "deleted_at"),
        Index("ix_invitations_created_at", "created_at"),
        Index("ix_invitations_inviter_created_at", "inviter_user_id", "created_at"),
        Index("ix_invitations_redeemed_by_user_id", "redeemed_by_user_id"),
        Index("ix_invitations_deactivated_by", "deactivated_by"),
    )
