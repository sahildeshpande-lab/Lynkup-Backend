from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from common.enums import ReactionType
from common.schemas import ApiResponse


REACTION_TYPE_DESCRIPTION = (
    "Reaction type. Allowed values: "
    + ", ".join(reaction_type.value for reaction_type in ReactionType)
    + ". Set to null to remove the current reaction."
)


class UpsertPostReactionRequest(BaseModel):
    post_id: UUID
    reaction_type: ReactionType | None = Field(
        default=None,
        description=REACTION_TYPE_DESCRIPTION,
        json_schema_extra={
            "enum": [None, *[reaction_type.value for reaction_type in ReactionType]],
        },
    )

    @field_validator("reaction_type", mode="before")
    @classmethod
    def normalize_reaction_type(cls, value: str | ReactionType | None) -> ReactionType | None:
        if value is None or value == "":
            return None
        if isinstance(value, ReactionType):
            return value
        normalized = str(value).strip().lower()
        try:
            return ReactionType(normalized)
        except ValueError as exc:
            raise ValueError(
                f"Invalid reaction_type '{value}'. "
                f"Allowed values: {', '.join(rt.value for rt in ReactionType)}"
            ) from exc


class PostReactionData(BaseModel):
    post_id: UUID
    like_count: int
    user_reaction: str | None = None


class PostReactionResponse(ApiResponse):
    data: PostReactionData | None = None


class RepostPostRequest(BaseModel):
    post_id: UUID


class RepostData(BaseModel):
    post_id: UUID
    is_reposted: bool
    repost_count: int


class RepostResponse(ApiResponse):
    data: RepostData | None = None


class SharePostRequest(BaseModel):
    post_id: UUID


class ShareResponse(ApiResponse):
    data: dict | None = None


class BookmarkRequest(BaseModel):
    post_id: UUID
    is_bookmarked: bool


class BookmarkData(BaseModel):
    post_id: UUID
    is_bookmarked: bool


class BookmarkResponse(ApiResponse):
    data: BookmarkData | None = None


class BookmarkListData(BaseModel):
    items: list[dict]
    page: int
    pageSize: int
    totalItems: int
    totalPages: int


class BookmarkListResponse(ApiResponse):
    data: BookmarkListData | None = None


class LikedPostsListData(BaseModel):
    items: list[dict]
    page: int
    pageSize: int
    totalItems: int
    totalPages: int


class LikedPostsListResponse(ApiResponse):
    data: LikedPostsListData | None = None


class CommentAuthor(BaseModel):
    profile_id: UUID | None = None
    first_name: str | None = None
    last_name: str | None = None
    university: str | None = None
    profilePhoto_url: str | None = None
    major: str | None = None
    minor: str | None = None
    edu_level: str | None = None
    bio: str | None = None


class EngagementAuthor(CommentAuthor):
    """Shared author profile shape for comments and post reactions."""


class ReactionSummaryItem(BaseModel):
    reaction_type: str
    count: int


class PostReactorProfile(BaseModel):
    profile_id: UUID | None = None
    first_name: str | None = None
    last_name: str | None = None
    profilePhoto_url: str | None = None
    bio: str | None = None
    reaction_type: str
    reacted_at: datetime


class PostReactionsGrouped(BaseModel):
    LIKE: list[PostReactorProfile] = Field(default_factory=list)
    CELEBRATE: list[PostReactorProfile] = Field(default_factory=list)
    INSIGHTFUL: list[PostReactorProfile] = Field(default_factory=list)
    SUPPORT: list[PostReactorProfile] = Field(default_factory=list)
    CURIOUS: list[PostReactorProfile] = Field(default_factory=list)


class PostReactionsListData(BaseModel):
    reactions: dict[str, list[PostReactorProfile]]
    summary: list[ReactionSummaryItem]
    page: int
    pageSize: int
    totalItems: int
    totalPages: int


class PostReactionsListResponse(ApiResponse):
    data: PostReactionsListData | None = None


def parse_reaction_type_filter(value: str | None) -> ReactionType | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    try:
        return ReactionType(normalized)
    except ValueError as exc:
        raise ValueError(
            f"Invalid reaction_type '{value}'. "
            f"Allowed values: {', '.join(rt.value for rt in ReactionType)}"
        ) from exc


def _parse_reaction_type(value: str | None) -> ReactionType | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    try:
        return ReactionType(normalized)
    except ValueError as exc:
        raise ValueError(
            f"Invalid reaction_type '{value}'. "
            f"Allowed values: {', '.join(rt.value for rt in ReactionType)}"
        ) from exc


class CreateCommentRequest(BaseModel):
    post_id: UUID
    comment_text: str = Field(min_length=1, max_length=5000)
    parent_comment_id: UUID | None = Field(
        default=None,
        description="Parent comment ID for replies. Omit for top-level comments.",
    )


class DeleteCommentRequest(BaseModel):
    post_id: UUID
    comment_id: UUID


class CommentData(BaseModel):
    id: UUID
    post_id: UUID
    parent_comment_id: UUID | None = None
    level: int
    is_deleted: bool
    like_count: int
    reply_count: int
    comment_text: str
    author: CommentAuthor
    user_reaction: str | None = None
    created_at: datetime
    updated_at: datetime
    replies: list["CommentData"] = Field(default_factory=list)


class CommentResponse(ApiResponse):
    data: CommentData | None = None


class CommentListData(BaseModel):
    comments: list[CommentData]
    total: int
    page: int
    limit: int
    pages: int


class CommentListResponse(ApiResponse):
    data: CommentListData | None = None


class UpsertCommentReactionRequest(BaseModel):
    comment_id: UUID
    reaction_type: ReactionType | None = Field(
        default=None,
        description=REACTION_TYPE_DESCRIPTION,
        json_schema_extra={
            "enum": [None, *[reaction_type.value for reaction_type in ReactionType]],
        },
    )

    @field_validator("reaction_type", mode="before")
    @classmethod
    def normalize_reaction_type(cls, value: str | ReactionType | None) -> ReactionType | None:
        if value is None or value == "":
            return None
        if isinstance(value, ReactionType):
            return value
        normalized = str(value).strip().lower()
        try:
            return ReactionType(normalized)
        except ValueError as exc:
            raise ValueError(
                f"Invalid reaction_type '{value}'. "
                f"Allowed values: {', '.join(rt.value for rt in ReactionType)}"
            ) from exc


class CommentReactionData(BaseModel):
    comment_id: UUID
    like_count: int
    user_reaction: str | None = None


class CommentReactionResponse(ApiResponse):
    data: CommentReactionData | None = None
