"""Shared unit-test fixtures.

The cleanup fixture only targets records created by tests. Test users should
use a recognizable email/firebase_uid prefix such as ``pytest`` or common
legacy prefixes already used in this suite.
"""
from __future__ import annotations

import logging

import pytest_asyncio
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.database.session import async_session_factory, engine

logger = logging.getLogger(__name__)


USER_EMAIL_PATTERNS = (
    "pytest%",
    "pytest_conn_%",
    "active_%",
    "pending_%",
    "user_%",
    "token_%",
    "pending_link_%",
    "existing_%",
    "new_%",
    "del_%",
    "inactive_%",
    "throttle_%",
    "forgot_%",
    "admin_%",
    "user%@example.com",
)

EMAIL_LOG_PATTERNS = USER_EMAIL_PATTERNS + ("test_send%", "test_queue%")


async def _purge_pytest_data(session: AsyncSession) -> None:
    email_filters = " OR ".join(f"email LIKE :email_pattern_{idx}" for idx, _ in enumerate(USER_EMAIL_PATTERNS))
    uid_filters = " OR ".join(f"firebase_uid LIKE :uid_pattern_{idx}" for idx, _ in enumerate(USER_EMAIL_PATTERNS))
    params = {
        **{f"email_pattern_{idx}": pattern for idx, pattern in enumerate(USER_EMAIL_PATTERNS)},
        **{f"uid_pattern_{idx}": pattern for idx, pattern in enumerate(USER_EMAIL_PATTERNS)},
    }

    result = await session.execute(
        text(f"SELECT id FROM users WHERE {email_filters} OR {uid_filters}"),
        params,
    )
    user_ids = [row[0] for row in result.fetchall()]

    if user_ids:
        user_filter = bindparam("user_ids", expanding=True)
        user_params = {"user_ids": user_ids}

        connection_tables = (
            ("connection_requests", "sender_user_id", "receiver_user_id"),
            ("connections", "user_low_id", "user_high_id"),
            ("follows", "follower_user_id", "following_user_id"),
            ("blocks", "blocker_user_id", "blocked_user_id"),
        )
        for table, col_a, col_b in connection_tables:
            await session.execute(
                text(
                    f"DELETE FROM {table} WHERE {col_a} IN :user_ids OR {col_b} IN :user_ids"
                ).bindparams(user_filter),
                user_params,
            )

        for table in (
            "security_events",
            "refresh_tokens",
            "user_installations",
            "consent_records",
            "profiles",
            "user_roles",
            "password_reset_tokens",
        ):
            await session.execute(
                text(f"DELETE FROM {table} WHERE user_id IN :user_ids").bindparams(user_filter),
                user_params,
            )

        await session.execute(
            text("DELETE FROM users WHERE id IN :user_ids").bindparams(user_filter),
            user_params,
        )

    log_filters = " OR ".join(f'"to" LIKE :log_pattern_{idx}' for idx, _ in enumerate(EMAIL_LOG_PATTERNS))
    log_params = {f"log_pattern_{idx}": pattern for idx, pattern in enumerate(EMAIL_LOG_PATTERNS)}
    await session.execute(text(f"DELETE FROM transactional_email_log WHERE {log_filters}"), log_params)
    await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def db_cleanup():
    """Purge test-owned rows before and after each test when a DB is available."""
    try:
        async with async_session_factory() as session:
            await _purge_pytest_data(session)
    except Exception as exc:
        logger.warning("Skipping pre-test DB cleanup: %s", exc)

    yield

    try:
        async with async_session_factory() as session:
            await _purge_pytest_data(session)
    except Exception as exc:
        logger.warning("Skipping post-test DB cleanup: %s", exc)

    await engine.dispose()
