import pytest
import pytest_asyncio
from core.database.session import engine, async_session_factory
from apps.accounts.db_models import User, RefreshToken, TransactionalEmailLog, SecurityEvent, UserRole
from apps.profiles.db_models import Profile
from sqlmodel import select, delete

@pytest_asyncio.fixture(autouse=True)
async def cleanup_db_connections():
    yield
    await engine.dispose()

@pytest_asyncio.fixture(autouse=True)
async def cleanup_test_records():
    yield
    async with async_session_factory() as session:
        from apps.accounts.db_models import User, RefreshToken, TransactionalEmailLog, SecurityEvent, UserRole, PasswordResetToken
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
