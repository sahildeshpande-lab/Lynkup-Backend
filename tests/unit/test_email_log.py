from __future__ import annotations

import pytest
from sqlmodel import select
from sqlalchemy import func

from core.db.session import async_session_factory, engine
from core.db.init import init_db
from apps.accounts.db_models import User, TransactionalEmailLog
from core.email_service import send_otp_email


@pytest.mark.asyncio
async def test_email_service_is_send_and_logging() -> None:
    try:
        await init_db()
        # 1. Create a dummy user in DB
        async with async_session_factory() as session:
            # Clean any existing logs to have a clean test
            for log in (await session.execute(select(TransactionalEmailLog))).scalars().all():
                await session.delete(log)
            
            # Clean existing test users if any
            stmt = select(User).where(
                (User.email == "test_send@example.com") |
                (User.firebase_uid == "test-send-uid")
            )
            for existing in (await session.execute(stmt)).scalars().all():
                await session.delete(existing)
            await session.commit()

        async with async_session_factory() as session:
            test_user = User(
                firebase_uid="test-send-uid",
                email="test_send@example.com",
                role="user",
            )
            session.add(test_user)
            await session.commit()

        # 2. Send email
        success = await send_otp_email("test_send@example.com", "123456", "email_verification")
        assert success is True
        
        async with async_session_factory() as session:
            stmt = select(TransactionalEmailLog).where(TransactionalEmailLog.to == "test_send@example.com")
            logs = (await session.execute(stmt)).scalars().all()
            assert len(logs) == 1
            assert logs[0].purpose == "OTP: email_verification"
            assert logs[0].subject == "KampuLynk Email Verification"
            assert logs[0].is_sent is True
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_email_log_limit_50() -> None:
    try:
        # Test capping at 50 logs
        async with async_session_factory() as session:
            # Clear existing logs first
            for log in (await session.execute(select(TransactionalEmailLog))).scalars().all():
                await session.delete(log)
            await session.commit()

            # Create 55 log entries
            for i in range(55):
                log_entry = TransactionalEmailLog(
                    to=f"user{i}@example.com",
                    from_email="no-reply@yourdomain.com",
                    body=f"Body {i}",
                    purpose="Test Cap",
                    subject=f"Subject {i}",
                    is_sent=True,
                )
                session.add(log_entry)
            await session.commit()

        # Trigger sending one more email (should trigger cleanup and cap it at 50 records)
        await send_otp_email("test_send@example.com", "123456", "email_verification")

        async with async_session_factory() as session:
            count = (await session.execute(select(func.count()).select_from(TransactionalEmailLog))).scalar_one()
            assert count == 50
    finally:
        await engine.dispose()
