from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from common.enums import MediaType
from common.schemas import ApiResponse

SHARE_CODE_MAX_LENGTH = 64


class SharePostRequest(BaseModel):
    code: str = Field(
        ...,
        min_length=1,
        max_length=SHARE_CODE_MAX_LENGTH,
        description="Branch share code returned by POST /share/link when type is share.",
        examples=["tPcFcGY2r6b"],
    )

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        stripped = (value or "").strip()
        if not stripped:
            raise ValueError("code is required")
        return stripped


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
    profile_visibility: str = "private"
    content: SharePostContentData
    media: list[SharePostMediaData] = Field(default_factory=list)
    created_at: datetime


class SharePostResponse(ApiResponse):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": True,
                "message": "Post retrieved successfully",
                "data": {
                    "post_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                    "author_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                    "author_name": "Ada Lovelace",
                    "profilePhoto_url": "https://cdn.example/p.png",
                    "profile_visibility": "public",
                    "content": {
                        "caption": "Campus update",
                        "content_html": "<p>Campus update</p>",
                        "visibility": "public",
                    },
                    "media": [],
                    "created_at": "2026-09-15T12:00:00Z",
                },
            }
        }
    )
    data: SharePostData | None = None


class ShareLinkType(str, Enum):
    invite = "invite"
    share = "share"


class ShareLinkRequest(BaseModel):
    type: ShareLinkType
    post_id: UUID | None = None

    @model_validator(mode="after")
    def validate_post_id_for_share(self):
        if self.type == ShareLinkType.share and self.post_id is None:
            raise ValueError("post_id is required when type is share")
        return self


class ShareLinkData(BaseModel):
    type: ShareLinkType
    code: str
    url: str


class ShareLinkResponse(ApiResponse):
    data: ShareLinkData | None = None
