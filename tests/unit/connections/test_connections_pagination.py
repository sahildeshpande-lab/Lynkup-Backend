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
from apps.connections.db_models import ConnectionRequest, Connection, Follow, Block
from apps.connections.schemas import RecommendedUserResponse, PendingLynkupRequestResponse


async def clean_pytest_connections_data(session):
    result = await session.execute(
        select(User).where(User.email.like("pytest_conn_%"))
    )
    users = result.scalars().all()
    user_ids = [u.id for u in users]
    if user_ids:
        params = {"user_ids": list(user_ids)}
        await session.execute(text("DELETE FROM connection_requests WHERE sender_user_id = ANY(:user_ids) OR receiver_user_id = ANY(:user_ids)"), params)
        await session.execute(text("DELETE FROM connections WHERE user_low_id = ANY(:user_ids) OR user_high_id = ANY(:user_ids)"), params)
        await session.execute(text("DELETE FROM follows WHERE follower_user_id = ANY(:user_ids) OR following_user_id = ANY(:user_ids)"), params)
        await session.execute(text("DELETE FROM blocks WHERE blocker_user_id = ANY(:user_ids) OR blocked_user_id = ANY(:user_ids)"), params)
        await session.execute(text("DELETE FROM profiles WHERE user_id = ANY(:user_ids)"), params)
        await session.execute(text("DELETE FROM users WHERE id = ANY(:user_ids)"), params)
        await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def connections_db_cleanup():
    async with async_session_factory() as session:
        await clean_pytest_connections_data(session)
    yield
    async with async_session_factory() as session:
        await clean_pytest_connections_data(session)


@pytest_asyncio.fixture
async def db_setup():
    await init_db()


@pytest_asyncio.fixture
async def test_users(db_setup):
    async with async_session_factory() as session:
        primary = User(
            email="pytest_conn_primary@example.com",
            role="user",
            firebase_uid=f"uid-conn-p-{uuid.uuid4()}",
            status="active"
        )
        session.add(primary)
        
        users = []
        names = [
            ("Alice", "Smith", "photo_alice.png"),
            ("Bob", "Jones", "photo_bob.png"),
            ("Charlie", "Brown", "photo_charlie.png"),
            ("David", "Smith", "photo_david.png"),
            ("Eve", "White", "photo_eve.png"),
        ]
        
        for idx, (first, last, photo) in enumerate(names, start=1):
            u = User(
                email=f"pytest_conn_{idx}@example.com",
                role="user",
                firebase_uid=f"uid-conn-{idx}-{uuid.uuid4()}",
                status="active"
            )
            session.add(u)
            users.append((u, first, last, photo))
            
        await session.commit()
        await session.refresh(primary)
        
        for u, _, _, _ in users:
            await session.refresh(u)
            
        # Create profiles
        p_primary = Profile(
            user_id=primary.id,
            first_name="Primary",
            last_name="User",
            completeness_rubric_version="v1"
        )
        session.add(p_primary)
        
        profiles = []
        for u, first, last, photo in users:
            p = Profile(
                user_id=u.id,
                first_name=first,
                last_name=last,
                profile_photo_url=photo,
                completeness_rubric_version="v1"
            )
            session.add(p)
            profiles.append(p)
            
        # Create pending requests: 1, 2, 3, 4 to primary. Eve (5) has no pending request.
        requests = []
        for u, _, _, _ in users[:4]:
            req = ConnectionRequest(
                sender_user_id=u.id,
                receiver_user_id=primary.id,
                status="pending"
            )
            session.add(req)
            requests.append(req)
            
        await session.commit()
        
    return primary, [u for u, _, _, _ in users]


@pytest.mark.asyncio
async def test_get_recommendations_no_pagination(test_users) -> None:
    primary, users = test_users
    
    async def _override_get_current_user():
        return primary
        
    app.dependency_overrides[get_current_user] = _override_get_current_user
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.get("/api/v1/recommendations/connections")
            assert response.status_code == 200
            body = response.json()
            assert body["status"] is True
            # Recommend Eve (5) because she has no pending connection request
            data = body["data"]
            assert isinstance(data, list)
            eve_recommendation = next((item for item in data if item["first_name"] == "Eve"), None)
            assert eve_recommendation is not None
            assert eve_recommendation["profile_photo_key"] == "photo_eve.png"
            assert eve_recommendation["first_name"] == "Eve"
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_get_recommendations_with_pagination(test_users) -> None:
    primary, users = test_users
    
    async def _override_get_current_user():
        return primary
        
    app.dependency_overrides[get_current_user] = _override_get_current_user
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            # We want more recommendations to test pagination. Let's make Eve (5) the only one still.
            # But let's verify pagination envelope is returned.
            response = await ac.get("/api/v1/recommendations/connections", params={"page": 1, "pageSize": 200})
            assert response.status_code == 200
            body = response.json()
            assert body["status"] is True
            data = body["data"]
            assert isinstance(data, dict)
            assert "items" in data
            assert data["page"] == 1
            assert data["pageSize"] == 200
            assert data["totalItems"] >= 1
            # Find Eve in items
            eve_recommendation = next((item for item in data["items"] if item["first_name"] == "Eve"), None)
            assert eve_recommendation is not None
            assert eve_recommendation["profile_photo_key"] == "photo_eve.png"
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_get_pending_requests_no_pagination(test_users) -> None:
    primary, users = test_users
    
    async def _override_get_current_user():
        return primary
        
    app.dependency_overrides[get_current_user] = _override_get_current_user
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.get("/api/v1/lynkup")
            assert response.status_code == 200
            body = response.json()
            assert body["status"] is True
            data = body["data"]
            assert isinstance(data, list)
            assert len(data) == 4
            # Verify profile details are present
            first_names = [d["first_name"] for d in data]
            assert "Alice" in first_names
            assert "Bob" in first_names
            assert "Charlie" in first_names
            assert "David" in first_names
            assert data[0]["profile_photo_key"] is not None
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_get_pending_requests_with_pagination(test_users) -> None:
    primary, users = test_users
    
    async def _override_get_current_user():
        return primary
        
    app.dependency_overrides[get_current_user] = _override_get_current_user
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.get("/api/v1/lynkup", params={"page": 1, "pageSize": 2})
            assert response.status_code == 200
            body = response.json()
            assert body["status"] is True
            data = body["data"]
            assert isinstance(data, dict)
            assert data["page"] == 1
            assert data["pageSize"] == 2
            assert data["totalItems"] == 4
            assert data["totalPages"] == 2
            assert len(data["items"]) == 2
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_get_pending_requests_with_search(test_users) -> None:
    primary, users = test_users
    
    async def _override_get_current_user():
        return primary
        
    app.dependency_overrides[get_current_user] = _override_get_current_user
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            # Search matches "Smith" (Alice Smith, David Smith)
            response = await ac.get("/api/v1/lynkup", params={"search": "Smith"})
            assert response.status_code == 200
            body = response.json()
            data = body["data"]
            assert isinstance(data, list)
            assert len(data) == 2
            first_names = [d["first_name"] for d in data]
            assert "Alice" in first_names
            assert "David" in first_names
            
            # Search matches "Charlie" (Charlie Brown)
            response_charlie = await ac.get("/api/v1/lynkup", params={"search": "Charlie"})
            assert response_charlie.status_code == 200
            data_charlie = response_charlie.json()["data"]
            assert len(data_charlie) == 1
            assert data_charlie[0]["first_name"] == "Charlie"
            assert data_charlie[0]["profile_photo_key"] == "photo_charlie.png"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
