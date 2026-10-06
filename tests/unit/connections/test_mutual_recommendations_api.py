from __future__ import annotations

from datetime import datetime, timezone
import uuid

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlmodel import select

from apps.accounts.db_models import Role, User, UserRole
from apps.connections.db_models import Block, Connection
from apps.connections.services.connection_service import build_connection_pair
from apps.profiles.db_models import Profile
from core.database.init import init_db
from core.database.session import async_session_factory
from core.security.auth import get_current_user
from entrypoints.api import app


EMAIL_PREFIX = "pytest_mutual_"


async def clean_pytest_mutual_data(session):
    result = await session.execute(select(User).where(User.email.like(f"{EMAIL_PREFIX}%")))
    users = result.scalars().all()
    user_ids = [user.id for user in users]
    if not user_ids:
        return
    params = {"user_ids": list(user_ids)}
    await session.execute(
        text(
            "DELETE FROM connection_requests WHERE sender_user_id = ANY(:user_ids) "
            "OR receiver_user_id = ANY(:user_ids)"
        ),
        params,
    )
    await session.execute(
        text(
            "DELETE FROM connections WHERE user_low_id = ANY(:user_ids) "
            "OR user_high_id = ANY(:user_ids)"
        ),
        params,
    )
    await session.execute(
        text(
            "DELETE FROM follows WHERE follower_user_id = ANY(:user_ids) "
            "OR following_user_id = ANY(:user_ids)"
        ),
        params,
    )
    await session.execute(
        text(
            "DELETE FROM blocks WHERE blocker_user_id = ANY(:user_ids) "
            "OR blocked_user_id = ANY(:user_ids)"
        ),
        params,
    )
    await session.execute(
        text("DELETE FROM notifications WHERE recipient_user_id = ANY(:user_ids)"),
        params,
    )
    await session.execute(
        text("DELETE FROM notification_preferences WHERE user_id = ANY(:user_ids)"),
        params,
    )
    await session.execute(text("DELETE FROM user_roles WHERE user_id = ANY(:user_ids)"), params)
    await session.execute(text("DELETE FROM profiles WHERE user_id = ANY(:user_ids)"), params)
    await session.execute(
        text("DELETE FROM user_activity_logs WHERE user_id = ANY(:user_ids)"),
        params,
    )
    await session.execute(text("DELETE FROM users WHERE id = ANY(:user_ids)"), params)
    await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def mutual_db_cleanup():
    async with async_session_factory() as session:
        await clean_pytest_mutual_data(session)
    yield
    async with async_session_factory() as session:
        await clean_pytest_mutual_data(session)


@pytest_asyncio.fixture
async def db_setup():
    await init_db()


async def _create_user(session, email_suffix: str, first_name: str, last_name: str, photo: str | None = None):
    user = User(
        email=f"{EMAIL_PREFIX}{email_suffix}@example.com",
        role="user",
        firebase_uid=f"uid-mutual-{email_suffix}-{uuid.uuid4()}",
        status="active",
    )
    session.add(user)
    await session.flush()
    session.add(
        Profile(
            user_id=user.id,
            first_name=first_name,
            last_name=last_name,
            profile_photo_url=photo,
            completeness_rubric_version="v1",
        )
    )
    return user


async def _ensure_user_role(session, users):
    user_role = (await session.execute(select(Role).where(Role.name == "user"))).scalar_one_or_none()
    if user_role is None:
        user_role = Role(name="user")
        session.add(user_role)
        await session.flush()
    for user in users:
        session.add(UserRole(user_id=user.id, role_id=user_role.id))


def _add_connection(session, user_a, user_b):
    low_id, high_id = build_connection_pair(user_a.id, user_b.id)
    session.add(Connection(user_low_id=low_id, user_high_id=high_id, is_active=True))


@pytest_asyncio.fixture
async def graph(db_setup):
    """Viewer connected to four friends; candidates with 1, 2, and 4 mutuals plus exclusions."""
    async with async_session_factory() as session:
        viewer = await _create_user(session, "viewer", "Primary", "User")
        alice = await _create_user(session, "alice", "Alice", "Adams", "photo_alice.png")
        bob = await _create_user(session, "bob", "Bob", "Baker", "photo_bob.png")
        cara = await _create_user(session, "cara", "Cara", "Cole", "photo_cara.png")
        dan = await _create_user(session, "dan", "Dan", "Dunn", "photo_dan.png")
        eve = await _create_user(session, "eve", "Eve", "One", "photo_eve.png")
        frank = await _create_user(session, "frank", "Frank", "Two", "photo_frank.png")
        grace = await _create_user(session, "grace", "Grace", "Four", "photo_grace.png")
        hank = await _create_user(session, "hank", "Hank", "Blocked", "photo_hank.png")
        ivy = await _create_user(session, "ivy", "Ivy", "Deleted", "photo_ivy.png")
        users = [viewer, alice, bob, cara, dan, eve, frank, grace, hank, ivy]
        await session.flush()
        await _ensure_user_role(session, users)

        for friend in (alice, bob, cara, dan):
            _add_connection(session, viewer, friend)

        _add_connection(session, alice, eve)
        _add_connection(session, alice, frank)
        _add_connection(session, bob, frank)
        for friend in (alice, bob, cara, dan):
            _add_connection(session, friend, grace)
        _add_connection(session, alice, hank)
        _add_connection(session, alice, ivy)

        session.add(Block(blocker_user_id=viewer.id, blocked_user_id=hank.id, is_active=True))
        ivy.is_deleted = True
        ivy.deleted_at = datetime.now(timezone.utc)

        await session.commit()
        for user in users:
            await session.refresh(user)

    return {
        "viewer": viewer,
        "alice": alice,
        "bob": bob,
        "cara": cara,
        "dan": dan,
        "eve": eve,
        "frank": frank,
        "grace": grace,
        "hank": hank,
        "ivy": ivy,
    }


def _get_as(user):
    async def _override_get_current_user():
        return user

    app.dependency_overrides[get_current_user] = _override_get_current_user
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_mutual_recommendations_no_connections(db_setup) -> None:
    async with async_session_factory() as session:
        viewer = await _create_user(session, "lonely", "Lonely", "User")
        other = await _create_user(session, "other", "Other", "User")
        await _ensure_user_role(session, [viewer, other])
        await session.commit()
        await session.refresh(viewer)

    try:
        async with _get_as(viewer) as client:
            response = await client.get("/api/v1/connections/mutual-recommendations")
            assert response.status_code == 200
            body = response.json()
            assert body["status"] is True
            data = body["data"]
            assert data["items"] == []
            assert data["page"] == 1
            assert data["totalItems"] == 0
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_mutual_recommendations_counts_users_exclusions_and_order(graph) -> None:
    viewer = graph["viewer"]
    try:
        async with _get_as(viewer) as client:
            response = await client.get("/api/v1/connections/mutual-recommendations")
            assert response.status_code == 200
            body = response.json()
            data = body["data"]
            items = data["items"]
            by_name = {item["first_name"]: item for item in items}

            assert "Alice" not in by_name
            assert "Hank" not in by_name
            assert "Ivy" not in by_name
            assert str(viewer.id) not in {item["user_id"] for item in items}

            assert by_name["Eve"]["mutual_connections"]["count"] == 1
            assert len(by_name["Eve"]["mutual_connections"]["users"]) == 1
            eve_mutual = by_name["Eve"]["mutual_connections"]["users"][0]
            assert eve_mutual["user_id"] == str(graph["alice"].id)
            assert eve_mutual["first_name"] == "Alice"
            assert eve_mutual["last_name"] == "Adams"
            assert eve_mutual["profilePhoto_url"].endswith("photo_alice.png")

            assert by_name["Frank"]["mutual_connections"]["count"] == 2
            assert len(by_name["Frank"]["mutual_connections"]["users"]) == 2

            assert by_name["Grace"]["mutual_connections"]["count"] == 4
            assert len(by_name["Grace"]["mutual_connections"]["users"]) == 3
            grace_names = [user["first_name"] for user in by_name["Grace"]["mutual_connections"]["users"]]
            assert grace_names == ["Alice", "Bob", "Cara"]
            for mutual in by_name["Grace"]["mutual_connections"]["users"]:
                assert set(mutual) >= {"user_id", "first_name", "last_name", "profilePhoto_url"}

            assert [item["first_name"] for item in items[:3]] == ["Grace", "Frank", "Eve"]
            for item in items:
                assert "score" not in item
                assert "match_reason" not in item
                assert "mutual_connections_count" not in item
                assert "user_id" in item
                assert "first_name" in item
                assert "university_details" in item
                assert item["is_connected"] is False
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_mutual_recommendations_pagination(graph) -> None:
    viewer = graph["viewer"]
    try:
        async with _get_as(viewer) as client:
            all_response = await client.get("/api/v1/connections/mutual-recommendations")
            all_data = all_response.json()["data"]
            total = all_data["totalItems"]
            assert total >= 3
            assert all_data["page"] == 1
            assert all_data["pageSize"] == total
            assert len(all_data["items"]) == total

            page_response = await client.get(
                "/api/v1/connections/mutual-recommendations",
                params={"page": 1, "pageSize": 2},
            )
            page_data = page_response.json()["data"]
            assert page_data["page"] == 1
            assert page_data["pageSize"] == 2
            assert page_data["totalItems"] == total
            assert page_data["totalPages"] == (total + 1) // 2
            assert len(page_data["items"]) == 2
            assert page_data["items"][0]["first_name"] == "Grace"

            page_two = await client.get(
                "/api/v1/connections/mutual-recommendations",
                params={"page": 2, "pageSize": 2},
            )
            page_two_data = page_two.json()["data"]
            assert page_two_data["page"] == 2
            assert len(page_two_data["items"]) >= 1
    finally:
        app.dependency_overrides.pop(get_current_user, None)
