from __future__ import annotations

import io
import json
import logging
import mimetypes
import pyzipper
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select, or_

from apps.accounts.db_models import User
from apps.connections.db_models import Block, Connection, ConnectionRequest, Follow
from apps.engagement.db_models import Bookmark, Comment, CommentReaction, PostReaction
from apps.feed.db_models import Post, PostAttachment
from apps.notifications.db_models import Notification, NotificationType
from apps.profiles.db_models import Profile
from core.images import config as image_config
from core.images.storage_service import get_media_url

logger = logging.getLogger(__name__)


def _json_default(obj: Any) -> Any:
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    raise TypeError(f"Object of type {type(obj)!r} is not JSON serializable")


def dumps_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default)


def _enum_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    return value


class DataExportBuilder:
    """Fetch user-owned data, serialize JSON, collect media, and build a ZIP."""

    def __init__(self, db: AsyncSession, user_id: UUID, export_id: UUID) -> None:
        self.db = db
        self.user_id = user_id
        self.export_id = export_id
        self._media_entries: list[tuple[str, bytes]] = []

    async def build_encrypted_zip_bytes(self, password: str) -> bytes:
        """Build a password-protected AES-256 ZIP archive.

        ``password`` must be exactly 6 uppercase alphanumeric characters as
        produced by :func:`apps.export.password.generate_export_password`.
        The password is used only for ZIP encryption; it is never stored or
        logged here.
        """
        files: dict[str, str | bytes] = {}

        profile_payload = await self.build_profile()
        files["profile.json"] = dumps_json(profile_payload)

        posts_payload = await self.build_posts()
        files["posts.json"] = dumps_json(posts_payload)

        comments_payload = await self.build_comments()
        files["comments.json"] = dumps_json(comments_payload)

        reactions_payload = await self.build_reactions()
        files["reactions.json"] = dumps_json(reactions_payload)

        bookmarks_payload = await self.build_bookmarks()
        files["bookmarks.json"] = dumps_json(bookmarks_payload)

        connections_payload = await self.build_connections()
        files["connections.json"] = dumps_json(connections_payload)

        notifications_payload = await self.build_notifications()
        files["notifications.json"] = dumps_json(notifications_payload)

        learning_payload = await self.build_learning_data()
        files["learning/learning_profile.json"] = dumps_json(
            learning_payload.get("learning_profile", {})
        )
        files["learning/recommendations.json"] = dumps_json(
            learning_payload.get("recommendations", [])
        )

        await self.build_media(profile_payload, posts_payload)

        generated_at = (
            datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        )
        files["README.txt"] = (
            "KampuLynk Data Export\n"
            "\n"
            f"Export ID: {self.export_id}\n"
            f"Generated At: {generated_at}\n"
            "\n"
            "This archive contains a copy of the personal data available for export\n"
            "from your KampuLynk account.\n"
            "\n"
            "The archive is password-protected. Use the password provided in the\n"
            "export email to extract the contents.\n"
        )

        buffer = io.BytesIO()
        with pyzipper.AESZipFile(
            buffer,
            mode="w",
            compression=pyzipper.ZIP_DEFLATED,
            encryption=pyzipper.WZ_AES,
        ) as zf:
            zf.setpassword(password.encode("utf-8"))
            for name, content in files.items():
                data = content.encode("utf-8") if isinstance(content, str) else content
                zf.writestr(name, data)
            for arcname, blob in self._media_entries:
                zf.writestr(arcname, blob)
        return buffer.getvalue()

    async def build_profile(self) -> dict[str, Any]:
        user = (
            await self.db.execute(select(User).where(User.id == self.user_id))
        ).scalar_one_or_none()
        profile = (
            await self.db.execute(select(Profile).where(Profile.user_id == self.user_id))
        ).scalar_one_or_none()

        payload: dict[str, Any] = {
            "id": self.user_id,
            "email": user.email if user else None,
            "status": _enum_value(user.status) if user else None,
            "onboarding_status": _enum_value(user.onboarding_status) if user else None,
            "email_verified_at": user.email_verified_at if user else None,
            "created_at": user.created_at if user else None,
            "updated_at": user.updated_at if user else None,
            "last_login_at": user.last_login_at if user else None,
        }
        if profile:
            payload.update(
                {
                    "first_name": profile.first_name,
                    "last_name": profile.last_name,
                    "bio": profile.bio,
                    "university_id": profile.university_id,
                    "profile_interests_id": profile.profile_interests_id,
                    "major": profile.major,
                    "minor": profile.minor,
                    "edu_level": profile.edu_level,
                    "graduation_date": profile.graduation_date,
                    "country_id": profile.country_id,
                    "location_text": profile.location_text,
                    "profile_visibility": _enum_value(profile.profile_visibility),
                    "online_presence_visible": profile.online_presence_visible,
                    "profile_photo_url": profile.profile_photo_url,
                    "banner_photo_url": profile.banner_photo_url,
                    "posts_count": profile.posts_count,
                    "followers_count": profile.followers_count,
                    "following_count": profile.following_count,
                    "profile_updated_at": profile.updated_at,
                }
            )
        return payload

    async def build_posts(self) -> list[dict[str, Any]]:
        stmt = (
            select(Post)
            .where(Post.author_user_id == self.user_id)
            .options(selectinload(Post.attachments).selectinload(PostAttachment.media_asset))
            .order_by(Post.created_at.asc())
        )
        posts = (await self.db.execute(stmt)).scalars().all()
        result: list[dict[str, Any]] = []
        for post in posts:
            media_refs = []
            for attachment in post.attachments or []:
                asset = attachment.media_asset
                if not asset:
                    continue
                media_refs.append(
                    {
                        "id": asset.id,
                        "key": asset.key,
                        "type": _enum_value(asset.type),
                        "original_filename": asset.original_filename,
                        "mime_type": asset.mime_type,
                        "url": get_media_url(asset.key) if asset.key else None,
                    }
                )
            content = post.content or {}
            result.append(
                {
                    "id": post.id,
                    "content": {
                        "caption": content.get("caption"),
                        "content_html": content.get("content_html"),
                        "visibility": content.get("visibility", "public"),
                    },
                    "state": _enum_value(post.state),
                    "visibility": content.get("visibility", "public"),
                    "is_edited": post.is_edited,
                    "revision_number": post.revision_number,
                    "like_count": post.like_count,
                    "repost_count": post.repost_count,
                    "share_count": post.share_count,
                    "comment_count": post.comment_count,
                    "media": media_refs,
                    "created_at": post.created_at,
                    "updated_at": post.updated_at,
                }
            )
        return result

    async def build_comments(self) -> list[dict[str, Any]]:
        stmt = (
            select(Comment)
            .where(Comment.user_id == self.user_id)
            .order_by(Comment.created_at.asc())
        )
        comments = (await self.db.execute(stmt)).scalars().all()
        return [
            {
                "id": comment.id,
                "post_id": comment.post_id,
                "parent_comment_id": comment.parent_comment_id,
                "comment_text": comment.comment_text if not comment.is_deleted else None,
                "level": comment.level,
                "is_deleted": comment.is_deleted,
                "created_at": comment.created_at,
                "updated_at": comment.updated_at,
            }
            for comment in comments
        ]

    async def build_reactions(self) -> list[dict[str, Any]]:
        post_reactions = (
            await self.db.execute(
                select(PostReaction)
                .where(PostReaction.user_id == self.user_id)
                .order_by(PostReaction.created_at.asc())
            )
        ).scalars().all()
        comment_reactions = (
            await self.db.execute(
                select(CommentReaction)
                .where(CommentReaction.user_id == self.user_id)
                .order_by(CommentReaction.created_at.asc())
            )
        ).scalars().all()

        payload: list[dict[str, Any]] = []
        for reaction in post_reactions:
            payload.append(
                {
                    "id": reaction.id,
                    "target": "post",
                    "post_id": reaction.post_id,
                    "reaction_type": _enum_value(reaction.reaction_type),
                    "created_at": reaction.created_at,
                }
            )
        for reaction in comment_reactions:
            payload.append(
                {
                    "id": reaction.id,
                    "target": "comment",
                    "comment_id": reaction.comment_id,
                    "reaction_type": _enum_value(reaction.reaction_type),
                    "created_at": reaction.created_at,
                }
            )
        return payload

    async def build_bookmarks(self) -> list[dict[str, Any]]:
        bookmarks = (
            await self.db.execute(
                select(Bookmark)
                .where(Bookmark.user_id == self.user_id)
                .order_by(Bookmark.created_at.asc())
            )
        ).scalars().all()
        return [
            {
                "id": bookmark.id,
                "post_id": bookmark.post_id,
                "created_at": bookmark.created_at,
                "updated_at": bookmark.updated_at,
            }
            for bookmark in bookmarks
        ]

    async def build_connections(self) -> dict[str, Any]:
        connections = (
            await self.db.execute(
                select(Connection).where(
                    or_(
                        Connection.user_low_id == self.user_id,
                        Connection.user_high_id == self.user_id,
                    )
                )
            )
        ).scalars().all()
        requests = (
            await self.db.execute(
                select(ConnectionRequest).where(
                    or_(
                        ConnectionRequest.sender_user_id == self.user_id,
                        ConnectionRequest.receiver_user_id == self.user_id,
                    )
                )
            )
        ).scalars().all()
        follows = (
            await self.db.execute(
                select(Follow).where(
                    or_(
                        Follow.follower_user_id == self.user_id,
                        Follow.following_user_id == self.user_id,
                    )
                )
            )
        ).scalars().all()
        blocks = (
            await self.db.execute(
                select(Block).where(Block.blocker_user_id == self.user_id)
            )
        ).scalars().all()

        return {
            "connections": [
                {
                    "id": row.id,
                    "connected_user_id": (
                        row.user_high_id if row.user_low_id == self.user_id else row.user_low_id
                    ),
                    "is_active": row.is_active,
                    "connected_at": row.connected_at,
                    "created_at": row.created_at,
                }
                for row in connections
            ],
            "requests": [
                {
                    "id": row.id,
                    "direction": "sent" if row.sender_user_id == self.user_id else "received",
                    "other_user_id": (
                        row.receiver_user_id
                        if row.sender_user_id == self.user_id
                        else row.sender_user_id
                    ),
                    "status": row.status,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                }
                for row in requests
            ],
            "follows": [
                {
                    "id": row.id,
                    "direction": (
                        "following" if row.follower_user_id == self.user_id else "follower"
                    ),
                    "other_user_id": (
                        row.following_user_id
                        if row.follower_user_id == self.user_id
                        else row.follower_user_id
                    ),
                    "is_active": row.is_active,
                    "created_at": row.created_at,
                }
                for row in follows
            ],
            "blocks": [
                {
                    "id": row.id,
                    "blocked_user_id": row.blocked_user_id,
                    "is_active": row.is_active,
                    "created_at": row.created_at,
                }
                for row in blocks
            ],
        }

    async def build_notifications(self) -> list[dict[str, Any]]:
        stmt = (
            select(Notification, NotificationType)
            .join(
                NotificationType,
                Notification.notification_type_id == NotificationType.id,
                isouter=True,
            )
            .where(Notification.recipient_user_id == self.user_id)
            .order_by(Notification.created_at.asc())
        )
        rows = (await self.db.execute(stmt)).all()
        return [
            {
                "id": notification.id,
                "type": notification_type.name if notification_type else None,
                "title": notification.title,
                "body": notification.body,
                "deep_link_payload": notification.deep_link_payload,
                "is_read": notification.is_read,
                "read_at": notification.read_at,
                "created_at": notification.created_at,
            }
            for notification, notification_type in rows
        ]

    async def build_learning_data(self) -> dict[str, Any]:
        """Export learning profile / recommendation history when available.

        Does not fail the export if learning tables or data are absent.
        """
        learning_profile: dict[str, Any] = {}
        recommendations: list[dict[str, Any]] = []
        try:
            profile = (
                await self.db.execute(
                    select(Profile).where(Profile.user_id == self.user_id)
                )
            ).scalar_one_or_none()
            if profile:
                learning_profile = {
                    "profile_interests_id": profile.profile_interests_id,
                    "learning_recommendations": profile.learning_recommendations,
                    "recommendations_updated_at": profile.recommendations_updated_at,
                }
            try:
                from apps.profiles.db_models import LearningRecommendationLog

                logs = (
                    await self.db.execute(
                        select(LearningRecommendationLog)
                        .where(LearningRecommendationLog.user_id == self.user_id)
                        .order_by(LearningRecommendationLog.created_at.asc())
                    )
                ).scalars().all()
                recommendations = [
                    {
                        "id": row.id,
                        "learning_recommendations": row.learning_recommendations,
                        "created_at": row.created_at,
                        "updated_at": row.updated_at,
                    }
                    for row in logs
                ]
            except Exception:
                logger.debug(
                    "Learning recommendation logs unavailable for export user=%s",
                    self.user_id,
                    exc_info=True,
                )
        except Exception:
            logger.warning(
                "Learning data export skipped for user=%s",
                self.user_id,
                exc_info=True,
            )
        return {
            "learning_profile": learning_profile,
            "recommendations": recommendations,
        }

    async def build_media(
        self,
        profile_payload: dict[str, Any],
        posts_payload: list[dict[str, Any]],
    ) -> None:
        """Collect profile and post media into the ZIP when retrievable."""
        profile_url = profile_payload.get("profile_photo_url")
        if profile_url:
            blob, ext = await self._fetch_media_bytes(str(profile_url))
            if blob:
                self._media_entries.append((f"media/profile/profile_photo{ext}", blob))

        banner_url = profile_payload.get("banner_photo_url")
        if banner_url:
            blob, ext = await self._fetch_media_bytes(str(banner_url))
            if blob:
                self._media_entries.append((f"media/profile/banner_photo{ext}", blob))

        for post in posts_payload:
            post_id = post.get("id")
            for index, media in enumerate(post.get("media") or [], start=1):
                key = media.get("key") or media.get("url")
                if not key:
                    continue
                blob, ext = await self._fetch_media_bytes(
                    str(key),
                    preferred_filename=media.get("original_filename"),
                    mime_type=media.get("mime_type"),
                )
                if not blob:
                    continue
                filename = media.get("original_filename")
                if filename:
                    safe_name = Path(str(filename)).name
                    arcname = f"media/posts/{post_id}/{safe_name}"
                else:
                    arcname = f"media/posts/{post_id}/image{index}{ext}"
                self._media_entries.append((arcname, blob))

    async def _fetch_media_bytes(
        self,
        key_or_url: str,
        *,
        preferred_filename: str | None = None,
        mime_type: str | None = None,
    ) -> tuple[bytes | None, str]:
        """Retrieve media via existing Spaces/local utilities when possible.

        Limitations (documented in apps/export/README.md):
        - Relies on existing ImageStorageSettings / s3_client credentials.
        - If Spaces is not configured and the object is not on local disk,
          remote HTTP download of public URLs is attempted as a fallback.
        """
        ext = self._guess_extension(key_or_url, preferred_filename, mime_type)
        key = self._extract_storage_key(key_or_url)

        # Prefer direct Spaces / S3 get_object using existing project client.
        bucket = image_config.settings.effective_bucket
        if bucket and key and not key.startswith("http"):
            try:
                response = image_config.s3_client.get_object(Bucket=bucket, Key=key)
                body = response["Body"].read()
                return body, ext
            except Exception:
                logger.debug("Spaces get_object failed for key=%s", key, exc_info=True)

        # Local uploads fallback used when Spaces is not configured.
        if key and not key.startswith("http"):
            local_base = (
                Path(__file__).resolve().parents[2]
                / "entrypoints"
                / "static"
                / "uploads"
            )
            candidates = [
                local_base / key,
                local_base / key.lstrip("/"),
            ]
            for candidate in candidates:
                if candidate.is_file():
                    return candidate.read_bytes(), ext

        # Public URL fallback (CDN / presigned / absolute URLs stored on profile).
        url = key_or_url
        if key and not key_or_url.startswith("http"):
            url = get_media_url(key) or key_or_url
        if url.startswith("http://") or url.startswith("https://"):
            try:
                import httpx

                async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                    response = await client.get(url)
                    if response.status_code == 200 and response.content:
                        return response.content, ext
            except Exception:
                logger.debug("HTTP media fetch failed for url=%s", url, exc_info=True)

        return None, ext

    @staticmethod
    def _extract_storage_key(key_or_url: str) -> str:
        value = (key_or_url or "").strip()
        if not value:
            return ""
        if value.startswith("http://") or value.startswith("https://"):
            parsed = urlparse(value)
            path = parsed.path.lstrip("/")
            # Strip bucket name prefix if present in path-style URLs.
            bucket = image_config.settings.effective_bucket
            if bucket and path.startswith(f"{bucket}/"):
                path = path[len(bucket) + 1 :]
            return path
        return value.lstrip("/")

    @staticmethod
    def _guess_extension(
        key_or_url: str,
        preferred_filename: str | None,
        mime_type: str | None,
    ) -> str:
        if preferred_filename and "." in preferred_filename:
            return "." + preferred_filename.rsplit(".", 1)[-1].lower()
        if mime_type:
            guessed = mimetypes.guess_extension(mime_type.split(";")[0].strip())
            if guessed:
                return ".jpg" if guessed == ".jpe" else guessed
        path = key_or_url.split("?")[0]
        if "." in path:
            return "." + path.rsplit(".", 1)[-1].lower()[:8]
        return ".bin"
