from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Literal, Any
from uuid import UUID
from pydantic import BaseModel, Field

from common.enums import MediaType
from apps.engagement.schemas import PostReactionsGrouped


from common.schemas import ApiResponse


class PostUploadData(BaseModel):
    id: UUID
    url: str
    key: str
    type: MediaType


class PostUploadResponse(ApiResponse):
    data: list[PostUploadData]


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

    - If ``id`` is omitted or null → create a new post.
    - If ``id`` is provided → update the existing post.

    ``is_draft``: ``true`` saves as draft on create or update; ``false``
    submits for processing. Saving a new draft replaces any existing draft
    for the same user.

    ``revision_number`` is never sent by the frontend;
    the backend manages it entirely.
    """
    id: Optional[UUID] = None
    is_draft: bool = False
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


class ReposterSummary(BaseModel):
    """Details of the user who reposted the original post."""
    id: UUID
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    profilePhoto_url: Optional[str] = None


class PostDetailData(BaseModel):
    id: UUID
    author_user_id: UUID
    state: str
    revision_number: int
    content: PostContentData
    created_at: datetime
    updated_at: datetime
    like_count: int = 0
    repost_count: int = 0
    share_count: int = 0
    comment_count: int = 0
    is_liked: bool = False
    is_reposted: bool = False
    is_bookmarked: bool = False
    user_reaction: str | None = None
    reactions: PostReactionsGrouped = Field(default_factory=PostReactionsGrouped)
    media: List[PostMediaData] = Field(default_factory=list)
    reposted_by: Optional[ReposterSummary] = None
