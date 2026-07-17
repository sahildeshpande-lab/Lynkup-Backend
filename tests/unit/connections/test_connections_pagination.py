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
from apps.accounts.db_models import User, Role, UserRole
from apps.profiles.db_models import Profile
from apps.profiles.db_models.profile_stats_db_model import ProfileStats
from apps.connections.db_models import ConnectionRequest, Connection, Follow, Block
from apps.connections.services.connection_service import build_connection_pair, respond_connection_request
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
        await session.execute(text("DELETE FROM user_roles WHERE user_id = ANY(:user_ids)"), params)
        await session.execute(
            text(
                "DELETE FROM profile_stats WHERE profile_id IN "
                "(SELECT id FROM profiles WHERE user_id = ANY(:user_ids))"
            ),
            params,
        )
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

        user_role = (await session.execute(select(Role).where(Role.name == "user"))).scalar_one_or_none()
        if user_role is None:
            user_role = Role(name="user")
            session.add(user_role)
            await session.flush()

        for u in [primary] + [user for user, _, _, _ in users]:
            session.add(UserRole(user_id=u.id, role_id=user_role.id))
        await session.commit()
            
        # Create profiles
        p_primary = Profile(
            user_id=primary.id,
            first_name="Primary",
            last_name="User",
            major="pytest_major_unique",
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
                major="pytest_major_unique" if first == "Eve" else None,
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
            data = body["data"]
            assert isinstance(data, dict)
            assert "items" in data
            assert data["page"] == 1
            assert data["totalPages"] == 1

            # Pending-request users are excluded from recommendations.
            alice_recommendation = next(
                (item for item in data["items"] if item["first_name"] == "Alice"),
                None,
            )
            assert alice_recommendation is None

            # Eve shares the same major and has no pending request.
            eve_recommendation = next(
                (item for item in data["items"] if item["first_name"] == "Eve"),
                None,
            )
            assert eve_recommendation is not None
            assert eve_recommendation["profilePhoto_url"].endswith("photo_eve.png")
            assert eve_recommendation["first_name"] == "Eve"
            assert eve_recommendation["is_deleted"] is False
            assert eve_recommendation["request_received"] is False
            assert eve_recommendation["mutual_connections_count"] == 0
            assert "Same major" in (eve_recommendation["match_reason"] or "")
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
            response = await ac.get(
                "/api/v1/recommendations/connections",
                params={"page": 1, "pageSize": 200},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["status"] is True
            data = body["data"]
            assert isinstance(data, dict)
            assert "items" in data
            assert data["page"] == 1
            assert data["pageSize"] == 200
            eve_recommendation = next(
                (item for item in data["items"] if item["first_name"] == "Eve"),
                None,
            )
            assert eve_recommendation is not None
            assert eve_recommendation["profilePhoto_url"].endswith("photo_eve.png")
            assert eve_recommendation["is_deleted"] is False
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_get_recommendations_includes_mutual_connection_friend(test_users) -> None:
    """A ↔ B and B ↔ C => A should see C via mutual connection."""
    primary, users = test_users
    alice, bob, charlie, david, eve = users

    async with async_session_factory() as session:
        # Clear pending requests so they don't block recommendations.
        pending = (
            await session.execute(
                select(ConnectionRequest).where(
                    ConnectionRequest.receiver_user_id == primary.id
                )
            )
        ).scalars().all()
        for req in pending:
            await session.delete(req)

        # primary ↔ bob, bob ↔ charlie (friends of friends)
        low_pb, high_pb = build_connection_pair(primary.id, bob.id)
        low_bc, high_bc = build_connection_pair(bob.id, charlie.id)
        session.add(Connection(user_low_id=low_pb, user_high_id=high_pb, is_active=True))
        session.add(Connection(user_low_id=low_bc, user_high_id=high_bc, is_active=True))
        await session.commit()

    async def _override_get_current_user():
        return primary

    app.dependency_overrides[get_current_user] = _override_get_current_user
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.get("/api/v1/recommendations/connections")
            assert response.status_code == 200
            items = response.json()["data"]["items"]

            # Direct connection is excluded.
            bob_item = next((item for item in items if item["user_id"] == str(bob.id)), None)
            assert bob_item is None

            charlie_item = next(
                (item for item in items if item["user_id"] == str(charlie.id)),
                None,
            )
            assert charlie_item is not None
            assert charlie_item["mutual_connections_count"] == 1
            assert "mutual connection" in (charlie_item["match_reason"] or "").lower()
            assert charlie_item["score"] >= 25
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
            assert data[0]["profilePhoto_url"] is not None
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_lynkup_lists_sent_and_received_pending_requests(test_users) -> None:
    primary, users = test_users
    alice = users[0]

    async def _override_primary():
        return primary

    app.dependency_overrides[get_current_user] = _override_primary
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            primary_response = await ac.get("/api/v1/lynkup")
            assert primary_response.status_code == 200
            primary_items = primary_response.json()["data"]
            alice_item = next(item for item in primary_items if item["user_id"] == str(alice.id))
            assert alice_item["request_received"] is True
            assert alice_item["request_sent"] is False
            assert alice_item["is_request"] is True
            assert alice_item["is_sent"] is False
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    async def _override_alice():
        return alice

    app.dependency_overrides[get_current_user] = _override_alice
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            alice_response = await ac.get("/api/v1/lynkup")
            assert alice_response.status_code == 200
            alice_items = alice_response.json()["data"]
            # Alice only has a sent request, so she should see 0 pending requests in her received list
            assert len(alice_items) == 0
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
            assert data_charlie[0]["profilePhoto_url"].endswith("photo_charlie.png")
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_list_connections(test_users) -> None:
    primary, users = test_users
    alice, bob = users[0], users[1]

    async with async_session_factory() as session:
        for other in (alice, bob):
            low_id, high_id = build_connection_pair(primary.id, other.id)
            session.add(Connection(user_low_id=low_id, user_high_id=high_id, is_active=True))
        await session.commit()

    async def _override_get_current_user():
        return primary

    app.dependency_overrides[get_current_user] = _override_get_current_user
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.get("/api/v1/connections")
            assert response.status_code == 200
            body = response.json()
            assert body["status"] is True
            assert body["message"] == "Connections fetched successfully"
            data = body["data"]
            assert isinstance(data, list)
            assert len(data) == 2
            first_names = {item["first_name"] for item in data}
            assert first_names == {"Alice", "Bob"}
            for item in data:
                assert item["status"] == "accepted"
                assert "lynkup_id" in item
                assert "user_id" in item
                assert "profilePhoto_url" in item

            paginated = await ac.get("/api/v1/connections", params={"page": 1, "pageSize": 1})
            assert paginated.status_code == 200
            page_data = paginated.json()["data"]
            assert isinstance(page_data, dict)
            assert page_data["page"] == 1
            assert page_data["pageSize"] == 1
            assert page_data["totalItems"] == 2
            assert page_data["totalPages"] == 2
            assert len(page_data["items"]) == 1
            assert page_data["items"][0]["status"] == "accepted"
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_lynkup_accept_increments_connection_count(test_users) -> None:
    primary, users = test_users
    alice = users[0]

    async with async_session_factory() as session:
        primary_profile = (await session.execute(
            select(Profile).where(Profile.user_id == primary.id)
        )).scalar_one()
        alice_profile = (await session.execute(
            select(Profile).where(Profile.user_id == alice.id)
        )).scalar_one()
        primary_profile_id = primary_profile.id
        alice_profile_id = alice_profile.id
        session.add(ProfileStats(profile_id=primary_profile_id, connection_count=0))
        session.add(ProfileStats(profile_id=alice_profile_id, connection_count=0))
        await session.commit()

    async with async_session_factory() as session:
        response = await respond_connection_request(session, primary.id, alice.id, "accepted")
        assert response.status is True
        assert response.data["is_connected"] is True

        primary_stats = (await session.execute(
            select(ProfileStats).where(ProfileStats.profile_id == primary_profile_id)
        )).scalar_one()
        alice_stats = (await session.execute(
            select(ProfileStats).where(ProfileStats.profile_id == alice_profile_id)
        )).scalar_one()
        assert primary_stats.connection_count == 1
        assert alice_stats.connection_count == 1


@pytest.mark.asyncio
async def test_lynkupresponse_accept_increments_connection_count_via_api(test_users) -> None:
    primary, users = test_users
    alice = users[0]

    async with async_session_factory() as session:
        primary_profile = (await session.execute(
            select(Profile).where(Profile.user_id == primary.id)
        )).scalar_one()
        alice_profile = (await session.execute(
            select(Profile).where(Profile.user_id == alice.id)
        )).scalar_one()
        primary_profile_id = primary_profile.id
        alice_profile_id = alice_profile.id
        session.add(ProfileStats(profile_id=primary_profile_id, connection_count=0))
        session.add(ProfileStats(profile_id=alice_profile_id, connection_count=0))
        await session.commit()

    async def _override_get_current_user():
        return primary

    app.dependency_overrides[get_current_user] = _override_get_current_user
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/lynkupresponse",
                json={
                    "receiver_user_id": str(alice.id),
                    "response": "accepted",
                },
            )
            assert response.status_code == 200
            body = response.json()
            assert body["status"] is True
            assert body["data"]["is_connected"] is True
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    async with async_session_factory() as session:
        primary_stats = (await session.execute(
            select(ProfileStats).where(ProfileStats.profile_id == primary_profile_id)
        )).scalar_one()
        alice_stats = (await session.execute(
            select(ProfileStats).where(ProfileStats.profile_id == alice_profile_id)
        )).scalar_one()
        assert primary_stats.connection_count == 1
        assert alice_stats.connection_count == 1


@pytest.mark.asyncio
async def test_lynkupremove_deletes_connection_and_decrements_counts(test_users) -> None:
    primary, users = test_users
    alice = users[0]
    low_id, high_id = build_connection_pair(primary.id, alice.id)

    async with async_session_factory() as session:
        primary_profile = (await session.execute(
            select(Profile).where(Profile.user_id == primary.id)
        )).scalar_one()
        alice_profile = (await session.execute(
            select(Profile).where(Profile.user_id == alice.id)
        )).scalar_one()
        primary_profile_id = primary_profile.id
        alice_profile_id = alice_profile.id
        session.add(Connection(user_low_id=low_id, user_high_id=high_id, is_active=True))
        session.add(ConnectionRequest(
            sender_user_id=primary.id,
            receiver_user_id=alice.id,
            status="accepted",
        ))
        session.add(ProfileStats(profile_id=primary_profile_id, connection_count=1))
        session.add(ProfileStats(profile_id=alice_profile_id, connection_count=1))
        await session.commit()

    async def _override_get_current_user():
        return primary

    app.dependency_overrides[get_current_user] = _override_get_current_user
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.request(
                "DELETE",
                "/api/v1/lynkupremove",
                json={"user_id": str(alice.id)},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["status"] is True
            assert body["message"] == "Connection removed successfully"
            assert body["data"]["is_connected"] is False
            assert body["data"]["request_sent"] is False
            assert body["data"]["request_received"] is False
            assert body["data"]["is_sent"] is False
            assert body["data"]["is_request"] is False
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    async with async_session_factory() as session:
        connection = (await session.execute(
            select(Connection).where(
                Connection.user_low_id == low_id,
                Connection.user_high_id == high_id,
            )
        )).scalar_one_or_none()
        assert connection is None
        request = (await session.execute(
            select(ConnectionRequest).where(
                ConnectionRequest.sender_user_id == primary.id,
                ConnectionRequest.receiver_user_id == alice.id,
                ConnectionRequest.status == "accepted",
            )
        )).scalar_one_or_none()
        assert request is None

        primary_stats = (await session.execute(
            select(ProfileStats).where(ProfileStats.profile_id == primary_profile_id)
        )).scalar_one()
        alice_stats = (await session.execute(
            select(ProfileStats).where(ProfileStats.profile_id == alice_profile_id)
        )).scalar_one()
        assert primary_stats.connection_count == 0
        assert alice_stats.connection_count == 0


@pytest.mark.asyncio
async def test_lynkupremove_returns_not_found_without_connection(test_users) -> None:
    primary, users = test_users
    eve = users[4]  # Eve has no pending request or active connection

    async def _override_get_current_user():
        return primary

    app.dependency_overrides[get_current_user] = _override_get_current_user
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.request(
                "DELETE",
                "/api/v1/lynkupremove",
                json={"user_id": str(eve.id)},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["status"] is False
            assert body["message"] == "Connection not found"
            assert body["data"] is None
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_lynkupremove_deletes_pending_request(test_users) -> None:
    primary, users = test_users
    alice = users[0]  # Alice has a pending request to primary

    async def _override_get_current_user():
        return primary

    app.dependency_overrides[get_current_user] = _override_get_current_user
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.request(
                "DELETE",
                "/api/v1/lynkupremove",
                json={"user_id": str(alice.id)},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["status"] is True
            assert body["message"] == "Connection removed successfully"
            assert body["data"]["is_connected"] is False
            assert body["data"]["request_sent"] is False
            assert body["data"]["request_received"] is False
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    async with async_session_factory() as session:
        # Check request is deleted from DB
        req = (await session.execute(
            select(ConnectionRequest).where(
                ConnectionRequest.sender_user_id == alice.id,
                ConnectionRequest.receiver_user_id == primary.id,
            )
        )).scalar_one_or_none()
        assert req is None
