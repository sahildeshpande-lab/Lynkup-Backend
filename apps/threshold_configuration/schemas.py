from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from common.schemas import ApiResponse

__all__ = [
    "ApiResponse",
    "ModerationThresholdsData",
    "UpdateModerationThresholdsRequest",
]


class ModerationThresholdsData(BaseModel):
    post: int
    comment: int
    user: int


class UpdateModerationThresholdsRequest(BaseModel):
    post: int | None = Field(default=None, gt=0)
    comment: int | None = Field(default=None, gt=0)
    user: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def require_at_least_one_field(self) -> UpdateModerationThresholdsRequest:
        if self.post is None and self.comment is None and self.user is None:
            raise ValueError("At least one of post, comment, or user must be provided")
        return self
