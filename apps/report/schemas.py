from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from common.enums import ReportEntityType, ReportStatus
from common.schemas import ApiResponse


class ReportCreateRequest(BaseModel):
    entity_type: ReportEntityType
    entity_id: UUID
    reason: str = Field(min_length=1, max_length=5000)


class ReportReviewRequest(BaseModel):
    report_id: UUID
    status: ReportStatus
    admin_comment: str | None = Field(default=None, max_length=5000)

    @field_validator("status")
    @classmethod
    def validate_review_status(cls, value: ReportStatus) -> ReportStatus:
        if value not in (ReportStatus.rejected, ReportStatus.actioned):
            raise ValueError("Status must be rejected or actioned.")
        return value


class ReportUserDetail(BaseModel):
    id: UUID
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None


class ReportDetailData(BaseModel):
    id: UUID
    reported_id: UUID
    entity_type: ReportEntityType
    entity_id: UUID
    reason: str
    status: ReportStatus
    moderator_id: UUID | None = None
    admin_comment: str | None = None
    created_at: datetime
    updated_at: datetime
    reporter_details: ReportUserDetail | None = None
    moderator_info: ReportUserDetail | None = None
    report_count: int = 0


class ReportResponse(ApiResponse):
    data: ReportDetailData | dict | None = None


class ReportListData(BaseModel):
    items: list[ReportDetailData]
    page: int
    pageSize: int
    totalItems: int
    totalPages: int


class ReportListResponse(ApiResponse):
    data: ReportListData | None = None
