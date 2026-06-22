"""
Root conftest for all unit tests.

Rules:
- Every test user is created with 'pytest' in its email or firebase_uid.
- The db_cleanup fixture runs BEFORE and AFTER every test to remove any
  pytest-tagged data, so no test data ever persists in the real DB.
- Existing (non-pytest) users are never touched.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
<<<<<<< HEAD
from core.database.session import engine, async_session_factory
from apps.accounts.db_models import User, RefreshToken, TransactionalEmailLog, SecurityEvent, UserRole
from apps.profiles.db_models import Profile
from sqlmodel import select, delete
=======
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.session import engine, async_session_factory


# ---------------------------------------------------------------------------
# Helper: delete all rows that belong to pytest test users
# ---------------------------------------------------------------------------

async def _purge_pytest_data(session: AsyncSession) -> None:
    """Delete every row created by pytest test cases (identified by 'pytest' in
    email or firebase_uid).  Foreign-key order matters.
    """
    result = await session.execute(
        text(
            "SELECT id FROM users "
            "WHERE email ILIKE '%pytest%' OR firebase_uid ILIKE '%pytest%'"
        )
    )
    user_ids = [row[0] for row in result.fetchall()]

    if user_ids:
        user_id_params = {"user_ids": user_ids}
        user_id_filter = bindparam("user_ids", expanding=True)

        profile_result = await session.execute(
            text("SELECT id FROM profiles WHERE user_id IN :user_ids").bindparams(user_id_filter),
            user_id_params,
        )
        profile_ids = [row[0] for row in profile_result.fetchall()]

        if profile_ids:
            await session.execute(
                text("DELETE FROM profile_interests WHERE profile_id IN :profile_ids").bindparams(
                    bindparam("profile_ids", expanding=True)
                ),
                {"profile_ids": profile_ids},
            )

        for table in (
            "security_events",
            "refresh_tokens",
            "user_identities",
            "user_installations",
            "consent_records",
            "profiles",
            "user_roles",
            "password_reset_tokens",
        ):
            await session.execute(
                text(f"DELETE FROM {table} WHERE user_id IN :user_ids").bindparams(user_id_filter),
                user_id_params,
            )

        await session.execute(
            text("DELETE FROM users WHERE id IN :user_ids").bindparams(user_id_filter),
            user_id_params,
        )

    # Clean up transactional email logs for pytest addresses
    await session.execute(
        text("DELETE FROM transactional_email_log WHERE \"to\" ILIKE '%pytest%'")
    )

    await session.commit()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
>>>>>>> 5038703 (Test cases)

@pytest_asyncio.fixture(autouse=True)
async def db_cleanup():
    """Autouse fixture: purge pytest data before AND after each test."""
    async with async_session_factory() as session:
        await _purge_pytest_data(session)

    yield

    async with async_session_factory() as session:
        await _purge_pytest_data(session)

    await engine.dispose()

@pytest_asyncio.fixture(autouse=True)
async def cleanup_test_records():
    yield
    async with async_session_factory() as session:
        from apps.accounts.db_models import User, RefreshToken, TransactionalEmailLog, SecurityEvent, UserRole, PasswordResetToken, UserInstallation
        from apps.profiles.db_models import Profile
        from sqlmodel import select, delete
        
        # Find all test users created in any unit tests
        stmt = select(User).where(
            User.email.like("active_%") |
            User.email.like("pending_%") |
            User.email.like("user_%") |
            User.email.like("token_%") |
            User.email.like("pending_link_%") |
            User.email.like("existing_%") |
            User.email.like("new_%") |
            User.email.like("del_%") |
            User.email.like("inactive_%") |
            User.email.like("throttle_%") |
            User.email.like("forgot_%") |
            User.email.like("test_send%") |
            User.email.like("admin_%") |
            User.email.like("user%@example.com")
        )
        test_users = (await session.execute(stmt)).scalars().all()
        for u in test_users:
            await session.execute(delete(RefreshToken).where(RefreshToken.user_id == u.id))
            await session.execute(delete(SecurityEvent).where(SecurityEvent.user_id == u.id))
            await session.execute(delete(UserRole).where(UserRole.user_id == u.id))
            await session.execute(delete(PasswordResetToken).where(PasswordResetToken.user_id == u.id))
            await session.execute(delete(UserInstallation).where(UserInstallation.user_id == u.id))
            await session.execute(delete(Profile).where(Profile.user_id == u.id))
            await session.delete(u)
            
        # Also clean up any test transactional email logs
        await session.execute(delete(TransactionalEmailLog).where(
            TransactionalEmailLog.to.like("active_%") |
            TransactionalEmailLog.to.like("pending_%") |
            TransactionalEmailLog.to.like("user_%") |
            TransactionalEmailLog.to.like("token_%") |
            TransactionalEmailLog.to.like("pending_link_%") |
            TransactionalEmailLog.to.like("existing_%") |
            TransactionalEmailLog.to.like("new_%") |
            TransactionalEmailLog.to.like("del_%") |
            TransactionalEmailLog.to.like("inactive_%") |
            TransactionalEmailLog.to.like("throttle_%") |
            TransactionalEmailLog.to.like("forgot_%") |
            TransactionalEmailLog.to.like("test_send%") |
            TransactionalEmailLog.to.like("test_queue%") |
            TransactionalEmailLog.to.like("admin_%") |
            TransactionalEmailLog.to.like("user%@example.com")
        ))
        await session.commit()
