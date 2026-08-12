from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from common.enums import MediaType
from common.schemas import ApiResponse


class SharePostRequest(BaseModel):
    post_id: UUID


class SharePostMediaData(BaseModel):
    id: UUID
    key: str
    type: MediaType
    url: str
    original_filename: Optional[str] = None
    mime_type: Optional[str] = None
    file_size: Optional[int] = None


class SharePostContentData(BaseModel):
    caption: Optional[str] = None
    content_html: Optional[str] = None
    visibility: str = "public"


class SharePostData(BaseModel):
    post_id: UUID
    author_id: UUID
    author_name: Optional[str] = None
    profilePhoto_url: Optional[str] = None
    profile_visibility: str = "public"
    content: SharePostContentData
    media: list[SharePostMediaData] = Field(default_factory=list)
    created_at: datetime


class SharePostResponse(ApiResponse):
    data: SharePostData | dict[str, Any] | None = None
