from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from apps.accounts.db_models import User
from apps.feed.db_models import Post, PostAttachment
from apps.feed.services.post_service import format_post_detail
from apps.profiles.db_models import Profile
from apps.share.schemas import SharePostData
from common.enums import FEED_VISIBLE_POST_STATES
from common.exceptions import ApiError
from common.user_visibility import is_hidden_account_status, visible_user_filters


async def get_shareable_post(db: AsyncSession, post_id: UUID) -> SharePostData:
    """Return a public share payload for a feed-visible published post.

    Unavailable posts (missing, non-public state/content, or hidden author)
    all surface as ``Post not found`` to avoid leaking why.
    """
    stmt = (
        select(Post, User, Profile)
        .join(User, User.id == Post.author_user_id)
        .outerjoin(Profile, Profile.user_id == Post.author_user_id)
        .where(Post.id == post_id)
        .where(Post.state.in_(FEED_VISIBLE_POST_STATES))
        .where(*visible_user_filters(User))
        .options(selectinload(Post.attachments).selectinload(PostAttachment.media_asset))
    )
    row = (await db.execute(stmt)).one_or_none()
    if row is None:
        raise ApiError("Post not found")

    post, author, profile = row

    # Defense in depth if status filters ever diverge from helpers.
    if is_hidden_account_status(author.status) or author.is_deleted or author.deleted_at is not None:
        raise ApiError("Post not found")

    visibility = (post.content or {}).get("visibility", "public")
    if visibility != "public":
        raise ApiError("Post not found")

    detail = format_post_detail(
        post,
        author_profile=profile,
        author_user=author,
    )

    content = detail.get("content") or {}
    media = detail.get("media") or []

    return SharePostData(
        post_id=detail["id"],
        author_id=detail["author_user_id"],
        author_name=detail.get("author_name"),
        profilePhoto_url=detail.get("profilePhoto_url"),
        profile_visibility=detail.get("profile_visibility", "public"),
        content={
            "caption": content.get("caption"),
            "content_html": content.get("content_html"),
            "visibility": content.get("visibility", "public"),
        },
        media=media,
        created_at=detail["created_at"],
    )
