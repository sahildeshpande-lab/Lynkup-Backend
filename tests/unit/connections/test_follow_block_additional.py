from __future__ import annotations

import uuid
import pytest
import pytest_asyncio
import httpx
from sqlmodel import select
from sqlalchemy import text

from entrypoints.api import app
from core.database.session import async_session_factory
from core.database.init import init_db
from core.security.auth import get_current_user
from apps.accounts.db_models import User
from apps.profiles.db_models import Profile
from apps.connections.db_models import Follow, Block
from apps.connections.services.follow_service import follow_user, unfollow_user
from apps.connections.services.block_service import block_user, unblock_user
from core.email_service import build_profile_updated_email_html, send_profile_updated_email


async def clean_pytest_additional_conn_data(session):
    result = await session.execute(
        select(User).where(User.email.like("pytest_add_conn_%"))
    )
    users = result.scalars().all()
    user_ids = [u.id for u in users]
    if user_ids:
        params = {"user_ids": list(user_ids)}
        await session.execute(text("DELETE FROM follows WHERE follower_user_id = ANY(:user_ids) OR following_user_id = ANY(:user_ids)"), params)
        await session.execute(text("DELETE FROM blocks WHERE blocker_user_id = ANY(:user_ids) OR blocked_user_id = ANY(:user_ids)"), params)
        await session.execute(text("DELETE FROM profiles WHERE user_id = ANY(:user_ids)"), params)
        await session.execute(text("DELETE FROM users WHERE id = ANY(:user_ids)"), params)
        await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def additional_conn_db_cleanup():
    async with async_session_factory() as session:
        await clean_pytest_additional_conn_data(session)
    yield
    async with async_session_factory() as session:
        await clean_pytest_additional_conn_data(session)


@pytest_asyncio.fixture
async def db_setup():
    await init_db()


@pytest_asyncio.fixture
async def test_users(db_setup):
    async with async_session_factory() as session:
        user1 = User(
            email="pytest_add_conn_1@example.com",
            role="user",
            firebase_uid=f"uid-add-conn-1-{uuid.uuid4()}",
            status="active"
        )
        user2 = User(
            email="pytest_add_conn_2@example.com",
            role="user",
            firebase_uid=f"uid-add-conn-2-{uuid.uuid4()}",
            status="active"
        )
        session.add(user1)
        session.add(user2)
        await session.commit()
        await session.refresh(user1)
        await session.refresh(user2)

        p1 = Profile(user_id=user1.id, first_name="User", last_name="One", completeness_score=10)
        p2 = Profile(user_id=user2.id, first_name="User", last_name="Two", completeness_score=20)
        session.add(p1)
        session.add(p2)
        await session.commit()

        return user1, user2


@pytest.mark.asyncio
async def test_follow_unfollow_endpoints(test_users):
    user1, user2 = test_users

    # Override get_current_user dependency to return user1
    app.dependency_overrides[get_current_user] = lambda: user1

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Follow user2
        resp = await client.post("/api/v1/follow", json={"following_user_id": str(user2.id)})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["follower_user_id"] == str(user1.id)
        assert data["following_user_id"] == str(user2.id)

        # 2. Prevent duplicate follow (business failure in response body)
        resp_dup = await client.post("/api/v1/follow", json={"following_user_id": str(user2.id)})
        assert resp_dup.status_code == 200
        assert resp_dup.json()["status"] is False
        assert resp_dup.json()["message"] == "Already following."

        # 3. Prevent self-follow (business failure in response body)
        resp_self = await client.post("/api/v1/follow", json={"following_user_id": str(user1.id)})
        assert resp_self.status_code == 200
        assert resp_self.json()["status"] is False

        # 4. Unfollow user2
        resp_unfollow = await client.request("DELETE", "/api/v1/follow", json={"following_user_id": str(user2.id)})
        assert resp_unfollow.status_code == 200
        assert resp_unfollow.json()["data"] == []

        # 5. Unfollow non-existent follow (business failure in response body)
        resp_unfollow_again = await client.request("DELETE", "/api/v1/follow", json={"following_user_id": str(user2.id)})
        assert resp_unfollow_again.status_code == 200
        assert resp_unfollow_again.json()["status"] is False

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_block_unblock_endpoints(test_users):
    user1, user2 = test_users

    app.dependency_overrides[get_current_user] = lambda: user1

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Block user2
        resp = await client.post("/api/v1/block", json={"blocked_user_id": str(user2.id)})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["blocker_user_id"] == str(user1.id)
        assert data["blocked_user_id"] == str(user2.id)

        # 2. Prevent duplicate block (business failure in response body)
        resp_dup = await client.post("/api/v1/block", json={"blocked_user_id": str(user2.id)})
        assert resp_dup.status_code == 200
        assert resp_dup.json()["status"] is False
        assert resp_dup.json()["message"] == "Already blocked."

        # 3. Prevent self-block (business failure in response body)
        resp_self = await client.post("/api/v1/block", json={"blocked_user_id": str(user1.id)})
        assert resp_self.status_code == 200
        assert resp_self.json()["status"] is False

        # 4. Unblock user2
        resp_unblock = await client.request("DELETE", "/api/v1/block", json={"blocked_user_id": str(user2.id)})
        assert resp_unblock.status_code == 200
        assert resp_unblock.json()["data"] == []

        # 5. Unblock non-existent block (business failure in response body)
        resp_unblock_again = await client.request("DELETE", "/api/v1/block", json={"blocked_user_id": str(user2.id)})
        assert resp_unblock_again.status_code == 200
        assert resp_unblock_again.json()["status"] is False

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_email_profile_updated():
    # Test HTML template builder
    html = build_profile_updated_email_html("John Doe")
    assert "Profile Updated Successfully" in html
    assert "John Doe" in html

    # Test queueing profile updated email
    async with async_session_factory() as session:
        # We clean up first to be sure
        await session.execute(text("DELETE FROM transactional_email_log WHERE \"to\" = 'profile_update@example.com'"))
        await session.commit()

        res = await send_profile_updated_email("profile_update@example.com", "John Doe")
        assert res is True
