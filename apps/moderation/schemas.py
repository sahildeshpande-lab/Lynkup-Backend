from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from common.schemas import ApiResponse


class ModerationWordsData(BaseModel):
    profanityWords: list[str] = Field(default_factory=list)


class UpdateModerationWordsRequest(BaseModel):
    profanityWords: list[str] = Field(default_factory=list)


class ModerationHistoryItem(BaseModel):
    id: UUID
    entity_id: UUID
    action_taken: str
    moderator_name: str | None = None
    comment: str | None = None
    action_taken_at: datetime | None = None
