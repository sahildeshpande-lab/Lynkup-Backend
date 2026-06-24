from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, Field

T = TypeVar("T")


class ApiResponse(BaseModel):
    status: bool = True
    message: str = "success"
    data: Any | None = None


class ConnectionRequestCreate(BaseModel):
    receiver_user_id: UUID


class ConnectionRequestRespond(BaseModel):
    request_id: UUID
    response: str = Field(..., description="Must be 'accepted' or 'declined'")


# Response Schemas for serialization
class ConnectionRequestResponse(BaseModel):
    id: UUID
    sender_user_id: UUID
    receiver_user_id: UUID
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ConnectionResponse(BaseModel):
    id: UUID
    user_low_id: UUID
    user_high_id: UUID
    connected_at: datetime
    is_active: bool

    class Config:
        from_attributes = True


class FollowResponse(BaseModel):
    id: UUID
    follower_user_id: UUID
    following_user_id: UUID
    is_active: bool

    class Config:
        from_attributes = True


class BlockResponse(BaseModel):
    id: UUID
    blocker_user_id: UUID
    blocked_user_id: UUID
    is_active: bool

    class Config:
        from_attributes = True


class RecommendedUserResponse(BaseModel):
    user_id: UUID
    score: float
    first_name: str | None = None
    last_name: str | None = None
    university_id: UUID | None = None
    major: str | None = None
    minor: str | None = None
    edu_level: str | None = None
