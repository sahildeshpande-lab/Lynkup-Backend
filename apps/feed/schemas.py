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


# ---- Content payload (nested inside request bodies) ----

class PostContentPayload(BaseModel):
    """Content payload for creating or updating a post."""
    caption: str = Field(..., max_length=255)
    content_html: Optional[str] = None
    visibility: Literal["public", "hidden"] = "public"


# ---- Request schemas ----

class EditPostContentPayload(BaseModel):
    """Payload for editing existing post fields, where all fields are optional."""
    caption: Optional[str] = Field(default=None, max_length=255)
    content_html: Optional[str] = None
    visibility: Optional[Literal["public", "hidden"]] = None


class EditPostRequest(BaseModel):
    """Request schema for PATCH /posts/."""
    id: UUID
    content: Optional[EditPostContentPayload] = None
    media: Optional[List[MediaItem]] = None


class SavePostRequest(BaseModel):
    """
    Unified create / update-draft request.

    - If ``id`` is omitted or null → create a new draft.
    - If ``id`` is provided → update the existing draft.

    ``revision_number`` is never sent by the frontend;
    the backend manages it entirely.
    """
    id: Optional[UUID] = None
    is_edit: bool = False
    content: PostContentPayload
    media: Optional[List[MediaItem]] = Field(default_factory=list)


class DeletePostRequest(BaseModel):
    id: UUID


# ---- Response data schemas ----

class SavePostData(BaseModel):
    id: UUID
    revision_number: int


class SavePostResponse(ApiResponse):
    data: SavePostData


class PostMediaData(BaseModel):
    id: UUID
    key: str
    type: MediaType
    url: str
    original_filename: Optional[str] = None
    mime_type: Optional[str] = None
    file_size: Optional[int] = None


class PostContentData(BaseModel):
    """Content data as returned in API responses."""
    caption: Optional[str] = None
    content_html: Optional[str] = None
    visibility: str = "public"


class PostDetailData(BaseModel):
    id: UUID
    author_user_id: UUID
    state: str
    revision_number: int
    content: PostContentData
    created_at: datetime
    updated_at: datetime
    media: List[PostMediaData] = Field(default_factory=list)
