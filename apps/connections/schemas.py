from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, Field

from common.enums import LynkupResponse

T = TypeVar("T")


from common.schemas import ApiResponse


class ConnectionRequestCreate(BaseModel):
    receiver_user_id: str


class ConnectionRequestRespond(BaseModel):
    receiver_user_id: str
    response: LynkupResponse


class ConnectionRemoveRequest(BaseModel):
    user_id: UUID


class FollowRequest(BaseModel):
    following_user_id: str


class BlockRequest(BaseModel):
    blocked_user_id: str


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
    university: str | None = None
    major: str | None = None
    minor: str | None = None
    edu_level: str | None = None
    profilePhoto_url: str | None = None
    is_deleted: bool = False
    is_connected: bool = False
    is_followed: bool = False
    is_blocked: bool = False
    request_sent: bool = False
    request_received: bool = False


class PendingLynkupRequestResponse(BaseModel):
    lynkup_id: UUID
    user_id: UUID
    status: str
    first_name: str | None = None
    last_name: str | None = None
    profilePhoto_url: str | None = None

    class Config:
        from_attributes = True
