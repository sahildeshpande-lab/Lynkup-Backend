from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from stream_chat import StreamChat

from apps.accounts.db_models import User
from apps.accounts.services.common_service import _fetch_user_profile
from apps.chat.config import settings
from apps.chat.schemas import StreamTokenData
from apps.profiles.services.response_service import _compose_full_name
from core.images import generate_profile_image_url

logger = logging.getLogger(__name__)

_stream_client: StreamChat | None = None


class StreamChatError(Exception):
    """Raised when Stream Chat configuration or API operations fail."""


def _ensure_stream_configured() -> None:
    if not settings.stream_api_key or not settings.stream_api_key.strip():
        raise StreamChatError("Stream Chat API key is not configured")
    if not settings.stream_secret_key or not settings.stream_secret_key.strip():
        raise StreamChatError("Stream Chat secret key is not configured")


def get_stream_client() -> StreamChat:
    """Return a reusable Stream Chat server client."""
    global _stream_client
    _ensure_stream_configured()
    if _stream_client is None:
        _stream_client = StreamChat(
            api_key=settings.stream_api_key.strip(),
            api_secret=settings.stream_secret_key.strip(),
        )
    return _stream_client


def build_stream_user_payload(
    user: User,
    profile: Any | None,
) -> dict[str, str | None]:
    full_name = _compose_full_name(
        profile.first_name if profile else None,
        profile.last_name if profile else None,
    )
    profile_photo_url = (
        generate_profile_image_url(profile.profile_photo_url)
        if profile and profile.profile_photo_url
        else None
    )

    return {
        "id": str(user.id),
        "name": full_name or None,
        "image": profile_photo_url,
    }


async def upsert_stream_user(user: User, db: AsyncSession) -> None:
    profile = await _fetch_user_profile(db, user)
    user_payload = build_stream_user_payload(user, profile)

    try:
        get_stream_client().upsert_user(user_payload)
        logger.info("Stream user upserted for user_id=%s", user.id)
    except StreamChatError:
        raise
    except Exception as exc:
        logger.exception("Stream user upsert failed for user_id=%s", user.id)
        raise StreamChatError("Failed to sync user with Stream Chat") from exc


async def sync_stream_user_on_auth(user: User, db: AsyncSession) -> None:
    """Best-effort Stream user sync during login. Never raises to callers."""
    try:
        await upsert_stream_user(user, db)
    except StreamChatError as exc:
        logger.warning("Stream user upsert skipped for user_id=%s: %s", user.id, exc)
    except Exception as exc:
        logger.exception("Stream user upsert failed for user_id=%s: %s", user.id, exc)


async def generate_stream_token(user: User) -> StreamTokenData:
    """Mint a new Stream Chat user token (called by FE via POST /chat/token after login)."""
    _ensure_stream_configured()

    try:
        stream_token = get_stream_client().create_token(str(user.id))
    except Exception as exc:
        logger.exception("Stream token generation failed for user_id=%s", user.id)
        raise StreamChatError("Failed to generate Stream token") from exc

    return StreamTokenData(stream_token=stream_token)


async def revoke_stream_user_tokens(user: User) -> None:
    """Invalidate all Stream tokens issued for this user up to now (logout)."""
    _ensure_stream_configured()
    before = datetime.now(timezone.utc)
    try:
        get_stream_client().revoke_user_token(str(user.id), before)
        logger.info("Stream tokens revoked for user_id=%s before=%s", user.id, before.isoformat())
    except StreamChatError:
        raise
    except Exception as exc:
        logger.exception("Stream token revoke failed for user_id=%s", user.id)
        raise StreamChatError("Failed to revoke Stream tokens") from exc


async def revoke_stream_user_tokens_best_effort(user: User) -> None:
    """Best-effort token revoke for logout flows. Never raises to callers."""
    try:
        await revoke_stream_user_tokens(user)
    except StreamChatError as exc:
        logger.warning("Stream token revoke skipped for user_id=%s: %s", user.id, exc)
    except Exception:
        logger.exception("Stream token revoke failed for user_id=%s", user.id)
