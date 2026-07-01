from __future__ import annotations

from pydantic import BaseModel, Field

from common.schemas import ApiResponse


class ModerationWordsData(BaseModel):
    spamWords: list[str] = Field(default_factory=list)
    profanityWords: list[str] = Field(default_factory=list)


class UpdateModerationWordsRequest(BaseModel):
    spamWords: list[str] = Field(default_factory=list)
    profanityWords: list[str] = Field(default_factory=list)
