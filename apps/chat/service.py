from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from stream_chat import StreamChatAsync

from apps.accounts.db_models import User
from apps.accounts.services.common_service import _fetch_user_profile
from apps.chat.config import settings
from apps.chat.schemas import StreamTokenData
from apps.profiles.services.response_service import _compose_full_name
from core.images import generate_profile_image_url

logger = logging.getLogger(__name__)


class StreamChatError(Exception):
    """Raised when Stream Chat configuration or API operations fail."""


def _ensure_stream_configured() -> None:
    if not settings.stream_api_key or not settings.stream_api_key.strip():
        raise StreamChatError("Stream Chat API key is not configured")
    if not settings.stream_secret_key or not settings.stream_secret_key.strip():
        raise StreamChatError("Stream Chat secret key is not configured")


@asynccontextmanager
async def _stream_client() -> AsyncIterator[StreamChatAsync]:
    _ensure_stream_configured()
    async with StreamChatAsync(
        api_key=settings.stream_api_key.strip(),
        api_secret=settings.stream_secret_key.strip(),
    ) as client:
        yield client


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
        "full_name": full_name or None,
        "profile_photo_url": profile_photo_url,
    }


async def upsert_stream_user(user: User, db: AsyncSession) -> None:
    profile = await _fetch_user_profile(db, user)
    user_payload = build_stream_user_payload(user, profile)

    async with _stream_client() as client:
        await client.upsert_user(user_payload)


async def sync_stream_user_on_auth(user: User, db: AsyncSession) -> None:
    """Best-effort Stream user sync during login. Never raises to callers."""
    try:
        await upsert_stream_user(user, db)
        logger.info("Stream user upserted for user_id=%s", user.id)
    except StreamChatError as exc:
        logger.warning("Stream user upsert skipped for user_id=%s: %s", user.id, exc)
    except Exception as exc:
        logger.exception("Stream user upsert failed for user_id=%s: %s", user.id, exc)


async def generate_stream_token(user: User, db: AsyncSession) -> StreamTokenData:
    _ensure_stream_configured()
    await upsert_stream_user(user, db)

    expires_in = settings.stream_token_expiry
    expiration_timestamp = int(time.time()) + expires_in

    async with _stream_client() as client:
        try:
            stream_token = client.create_token(str(user.id), exp=expiration_timestamp)
        except Exception as exc:
            logger.exception("Stream token generation failed for user_id=%s", user.id)
            raise StreamChatError("Failed to generate Stream token") from exc

    return StreamTokenData(stream_token=stream_token, expires_in=expires_in)
