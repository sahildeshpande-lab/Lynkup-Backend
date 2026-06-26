from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Literal, Any
from uuid import UUID
from pydantic import BaseModel, Field

from common.enums import MediaType


class ApiResponse(BaseModel):
    status: bool = True
    message: str = "success"
    data: Any | None = None


class PostUploadData(BaseModel):
    id: UUID
    key: str
    type: MediaType
    url: str


class PostUploadResponse(ApiResponse):
    data: PostUploadData


class MediaItem(BaseModel):
    id: UUID
    type: MediaType


class CreatePostRequest(BaseModel):
    caption: Optional[str] = Field(default=None, max_length=255)
    text: Optional[str] = None
    media: Optional[List[MediaItem]] = Field(default_factory=list)
    visibility: Literal["public", "hidden"]


class CreatePostData(BaseModel):
    id: UUID


class CreatePostResponse(ApiResponse):
    data: CreatePostData


class UpdatePostRequest(BaseModel):
    caption: str = Field(..., max_length=255)  # Mandatory
    text: Optional[str] = None
    media: Optional[List[MediaItem]] = Field(default_factory=list)
    visibility: Optional[Literal["public", "hidden"]] = None


class PostMediaData(BaseModel):
    id: UUID
    key: str
    type: MediaType
    url: str
    original_filename: Optional[str] = None
    mime_type: Optional[str] = None
    file_size: Optional[int] = None


class PostDetailData(BaseModel):
    id: UUID
    author_user_id: UUID
    caption: Optional[str] = None
    content_html: Optional[str] = None
    state: str
    created_at: datetime
    updated_at: datetime
    media: List[PostMediaData] = Field(default_factory=list)
