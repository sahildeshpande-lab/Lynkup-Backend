from __future__ import annotations

import pytest
import uuid
import jwt
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException
from sqlmodel import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from core.database.session import async_session_factory, engine
from core.database.init import init_db

from apps.accounts.db_models import User, SecurityEvent, SecurityEventType, TransactionalEmailLog, RefreshToken
from apps.profiles.db_models import Profile
from common.enums import OnboardingStatus, RegistrationType, UserStatus
from apps.accounts.schemas import RefreshTokenRequest, LoginRequest
from apps.accounts.services import (
    _log_email_event,
    _generate_tokens,
    _store_refresh_token,
    _revoke_refresh_token_row,
    _refresh_token_payload,
    _build_auth_user_response,
    _registration_type_from_firebase,
    _display_name_from_firebase,
    _as_aware_utc,
    log_security_event,
    complete_firebase_registration,
    _fetch_user_profile,
    _issue_auth_session,
    build_firebase_session_response,
    AccountExistsException,
)

@pytest.mark.asyncio
async def test_accounts_basic_helpers() -> None:
    # _registration_type_from_firebase
    assert _registration_type_from_firebase({"firebase": {"sign_in_provider": "google.com"}}) == RegistrationType.google
    assert _registration_type_from_firebase({"firebase": {"sign_in_provider": "apple.com"}}) == RegistrationType.apple
    assert _registration_type_from_firebase({"firebase": {"sign_in_provider": "password"}}) == RegistrationType.email

    # _display_name_from_firebase
    assert _display_name_from_firebase({"name": " John Doe "}, "john@example.com") == "John Doe"
    assert _display_name_from_firebase({}, "john@example.com") == "john"

    # _as_aware_utc
    dt_naive = datetime(2026, 6, 15)
    dt_aware = _as_aware_utc(dt_naive)
    assert dt_aware.tzinfo == timezone.utc


@pytest.mark.asyncio
async def test_accounts_complete_firebase_registration() -> None:
    try:
        await init_db()
        async with async_session_factory() as session:
            # 1. Test registration of a new user
            firebase_user = {
                "uid": f"uid-{uuid.uuid4()}",
                "email": "user_reg_new@example.com",
                "email_verified": True,
                "name": "New User",
                "firebase": {"sign_in_provider": "google.com"}
            }
            user = await complete_firebase_registration(firebase_user, session)
            assert user.email == "user_reg_new@example.com"
            assert user.registration_type == RegistrationType.google

            # Verify profile created
            profile = await _fetch_user_profile(session, user)
            assert profile is not None
            assert profile.first_name == "New"
            assert profile.last_name == "User"

            # 2. Test registration of the same user (existing user case)
            user_again = await complete_firebase_registration(firebase_user, session)
            assert user_again.id == user.id

            # 3. Test recreated Firebase user (different uid, same email and sign_in_provider)
            recreated_firebase_user = {
                "uid": f"uid-{uuid.uuid4()}",
                "email": "user_reg_new@example.com",
                "email_verified": True,
                "name": "Recreated New User",
                "firebase": {"sign_in_provider": "google.com"}
            }
            user_recreated = await complete_firebase_registration(recreated_firebase_user, session)
            assert user_recreated.id == user.id
            assert user_recreated.firebase_uid == recreated_firebase_user["uid"]

            # 4. Test provider mismatch conflict
            mismatch_firebase_user = {
                "uid": f"uid-{uuid.uuid4()}",
                "email": "user_reg_new@example.com",
                "email_verified": True,
                "name": "Mismatch User",
                "firebase": {"sign_in_provider": "password"}
            }
            with pytest.raises(AccountExistsException):
                await complete_firebase_registration(mismatch_firebase_user, session)

    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_accounts_security_and_email_logging() -> None:
    try:
        await init_db()
        async with async_session_factory() as session:
            user = User(
                firebase_uid=f"uid-{uuid.uuid4()}",
                email="user_log_test@example.com",
                role="user",
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)

            # Test log_security_event
            await log_security_event(session, user.id, SecurityEventType.TOKEN_REVOKED)
            await session.commit()

            # Verify event logged
            stmt = select(SecurityEvent).where(SecurityEvent.user_id == user.id)
            event = (await session.execute(stmt)).scalars().first()
            assert event is not None
            assert event.event_type == SecurityEventType.TOKEN_REVOKED

            # Test _log_email_event
            await _log_email_event(
                session,
                to_email="user_log_test@example.com",
                subject="Test Subject",
                body="Test Body",
                purpose="Verification",
            )
            await session.commit()

            # Verify email log
            log_stmt = select(TransactionalEmailLog).where(TransactionalEmailLog.to == "user_log_test@example.com")
            email_log = (await session.execute(log_stmt)).scalars().first()
            assert email_log is not None
            assert email_log.subject == "Test Subject"

    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_accounts_session_management() -> None:
    try:
        await init_db()
        async with async_session_factory() as session:
            user = User(
                firebase_uid=f"uid-{uuid.uuid4()}",
                email="user_session_test@example.com",
                role="user",
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)

            # _issue_auth_session
            res = await _issue_auth_session(user, session)
            assert res["user"]["firebase_uid"] == user.firebase_uid
            assert "user" in res

            # _revoke_refresh_token_row
            token_row = RefreshToken(
                user_id=user.id,
                token_hash="some-hash",
                created_at=datetime.now(timezone.utc),
            )
            session.add(token_row)
            await session.commit()
            await session.refresh(token_row)
            assert token_row is not None
            assert token_row.revoked_at is None

            await _revoke_refresh_token_row(session, token_row)
            await session.commit()
            assert token_row.revoked_at is not None

            # build_firebase_session_response
            res_fb = await build_firebase_session_response(user, session)
            assert "user" in res_fb
            assert res_fb["user"]["firebase_uid"] == user.firebase_uid

    finally:
        await engine.dispose()


