from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, Enum as SqlEnum, Index, Text, UniqueConstraint, text
from sqlalchemy.orm import relationship
from sqlmodel import Field, Relationship, SQLModel

from common.enums import ReportEntityType, ReportStatus

if TYPE_CHECKING:
    from apps.accounts.db_models import User


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Report(SQLModel, table=True):
    __tablename__ = "reports"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    reported_id: UUID = Field(foreign_key="users.id", nullable=False)
    entity_type: ReportEntityType = Field(
        sa_column=Column(
            SqlEnum(ReportEntityType, name="reportentitytype"),
            nullable=False,
            index=True,
        )
    )
    entity_id: UUID = Field(nullable=False, index=True)
    reason: str = Field(sa_column=Column(Text, nullable=False))
    status: ReportStatus = Field(
        default=ReportStatus.under_review,
        sa_column=Column(
            SqlEnum(ReportStatus, name="reportstatus"),
            nullable=False,
            default=ReportStatus.under_review
        ),
    )
    moderator_id: UUID | None = Field(
        default=None,
        foreign_key="users.id",
        nullable=True,
    )
    admin_comment: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            onupdate=utc_now,
        ),
    )

    reporter: "User" = Relationship(
        sa_relationship=relationship("User", foreign_keys="Report.reported_id")
    )
    moderator: "User | None" = Relationship(
        sa_relationship=relationship("User", foreign_keys="Report.moderator_id")
    )

    __table_args__ = (
        Index("ix_reports_who_reported_id", "reported_id"),
        Index("ix_reports_moderator_id", "moderator_id"),
        Index("ix_reports_status", "status"),
        Index("ix_reports_created_at_desc", text("created_at DESC")),
        Index("ix_reports_composite_entity", "entity_type", "entity_id"),
        Index("ix_reports_composite_status_created_at", "status", text("created_at DESC")),
        UniqueConstraint("reported_id", "entity_type", "entity_id", name="uq_reports_reported_entity"),
    )
