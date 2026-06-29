from fastapi.testclient import TestClient

from apps.accounts.db_models import User
from apps.search import routes as search_routes
from core.database.session import get_session
from core.security.auth import get_current_user
from entrypoints.api import app


client = TestClient(app)


async def _override_current_user():
    return User(email="jane@example.com", role="user", firebase_uid="test-uid")


class _NoopSession:
    async def execute(self, *_args, **_kwargs):
        raise AssertionError("db session should not be used in this route test")

    def add(self, *_args, **_kwargs):
        return None

    async def commit(self):
        return None

    async def refresh(self, *_args, **_kwargs):
        return None


def setup_module() -> None:
    app.dependency_overrides[get_current_user] = _override_current_user
    async def _override_session():
        yield _NoopSession()
    app.dependency_overrides[get_session] = _override_session


def teardown_module() -> None:
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_session, None)


async def _search_universities(_params, _db) -> dict:
    return {
        "query": "kampu",
        "items": [
            {
                 "id": "uni-1",
                 "name": "Kampu University",
                 "country": "United States",
                 "slug": "kampu-university",
                 "major": [{"name": "Computer Science"}],
                 "minor": [{"name": "Psychology"}],
                 "academic_program": [{"name": "Undergraduate"}],
            }
        ],
        "page": 1,
        "pageSize": 20,
        "totalItems": 1,
        "totalPages": 1,
    }


def test_university_search_rejects_short_query() -> None:
    response = client.get("/api/v1/universities", params={"query": "ab", "page": 1, "pageSize": 20})

    assert response.status_code == 400


def test_university_search_returns_matches(monkeypatch) -> None:
    monkeypatch.setattr(search_routes.services, "search_universities", _search_universities)

    response = client.get("/api/v1/universities", params={"query": "kampu", "page": 1, "pageSize": 20})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Universities fetched successfully"
    assert body["data"]["page"] == 1
    assert body["data"]["pageSize"] == 20
    assert body["data"]["totalItems"] == 1
    assert body["data"]["items"][0]["name"] == "Kampu University"
    assert body["data"]["items"][0]["country"] == "United States"
    assert body["data"]["items"][0]["minor"] == [{"name": "Psychology"}]


async def _get_academics_info(query, page, page_size, db) -> dict:
    return {
        "interests": {
            "items": [{"id": "int-1", "name": "Math"}],
            "page": page,
            "pageSize": page_size,
            "totalItems": 1,
            "totalPages": 1,
        },
        "educationLevels": [{"id": "1", "name": "Bachelors"}, {"id": "2", "name": "Masters"}],
    }


def test_get_academics_info_returns_success(monkeypatch) -> None:
    monkeypatch.setattr(search_routes.services, "get_academics_info", _get_academics_info)

    response = client.get("/api/v1/academicsinfo", params={"query": "Math", "page": 1, "pageSize": 20})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Academics info fetched successfully"
    assert "interests" in body["data"]
    assert "educationLevels" in body["data"]
    assert body["data"]["interests"]["items"][0]["name"] == "Math"
    assert {"id": "1", "name": "Bachelors"} in body["data"]["educationLevels"]


def test_search_users_route(monkeypatch) -> None:
    async def _mock_search_users(current_user, db, query, page, page_size):
        return {
            "items": [
                {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "firstName": "John",
                    "lastName": "Doe",
                    "email": "john.doe@example.com",
                    "role": "user",
                    "university": "Kampu University",
                }
            ],
            "page": page or 1,
            "pageSize": page_size or 1,
            "totalItems": 1,
            "totalPages": 1,
        }

    monkeypatch.setattr(search_routes.services, "search_users", _mock_search_users)

    response = client.get(
        "/api/v1/search",
        params={
            "query": "John Kampu CS Math Bachelors",
            "page": 1,
            "pageSize": 10,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Search results fetched successfully"
    assert body["data"]["items"][0]["firstName"] == "John"
    assert body["data"]["items"][0]["email"] == "john.doe@example.com"


import pytest
from core.database.init import init_db
from core.database.session import async_session_factory, engine
from sqlmodel import select
from apps.search.services import search_users
from apps.accounts.db_models import Role, UserRole
from apps.profiles.db_models import Profile, Country
from apps.profiles.db_models.university_db_model import University
from apps.connections.db_models.block import Block
from apps.connections.db_models.connection import Connection


@pytest.mark.asyncio
async def test_search_users_service_logic(monkeypatch) -> None:
    # Mock file_exists to return True
    monkeypatch.setattr("core.images.file_exists", lambda key: True)
    # Mock normalize_image_name
    monkeypatch.setattr("core.images.normalize_image_name", lambda key: f"normalized/{key}")

    try:
        await init_db()
        async with async_session_factory() as session:
            import uuid
            unique_id = str(uuid.uuid4())[:8]

            # 1. Create a searcher user
            searcher = User(email=f"searcher_{unique_id}@example.com", role="user", firebase_uid=f"uid_searcher_{unique_id}")
            # 2. Create matching target user (active user)
            target1 = User(email=f"target1_{unique_id}@example.com", role="user", firebase_uid=f"uid_target1_{unique_id}", status="active")
            # 3. Create a moderator user (should be excluded)
            mod = User(email=f"mod_{unique_id}@example.com", role="moderator", firebase_uid=f"uid_mod_{unique_id}", status="active")
            # 4. Create an inactive target user (should be excluded)
            inactive_target = User(email=f"inactive_{unique_id}@example.com", role="user", firebase_uid=f"uid_inactive_{unique_id}", status="pending")

            session.add(searcher)
            session.add(target1)
            session.add(mod)
            session.add(inactive_target)
            await session.commit()
            
            await session.refresh(searcher)
            await session.refresh(target1)
            await session.refresh(mod)
            await session.refresh(inactive_target)

            # Assign roles
            user_role_obj = (await session.execute(select(Role).where(Role.name == "user"))).scalar_one()
            mod_role_obj = (await session.execute(select(Role).where(Role.name == "moderator"))).scalar_one()

            session.add(UserRole(user_id=searcher.id, role_id=user_role_obj.id))
            session.add(UserRole(user_id=target1.id, role_id=user_role_obj.id))
            session.add(UserRole(user_id=mod.id, role_id=mod_role_obj.id))
            session.add(UserRole(user_id=inactive_target.id, role_id=user_role_obj.id))
            await session.commit()


            # Create profiles
            country = Country(name="United States", iso_code=f"U{unique_id[:1]}")
            session.add(country)
            await session.commit()
            await session.refresh(country)

            uni = University(name=f"Kampu_{unique_id}", slug=f"kampu-uni-{unique_id}", country_id=country.id)
            session.add(uni)
            await session.commit()
            await session.refresh(uni)


            prof_searcher = Profile(user_id=searcher.id, first_name="Searcher", last_name="User", completeness_score=100)
            prof_target1 = Profile(user_id=target1.id, first_name=f"Target_{unique_id}", last_name="One", completeness_score=100, university_id=uni.id, major=f"CS_{unique_id}", edu_level="Bachelors")
            prof_inactive = Profile(user_id=inactive_target.id, first_name="Inactive", last_name="User", completeness_score=100)

            session.add(prof_searcher)
            session.add(prof_target1)
            session.add(prof_inactive)
            await session.commit()


        # Run search query
        async with async_session_factory() as session:
            searcher_db = (await session.execute(select(User).where(User.id == searcher.id))).scalar_one()

            # Test 1: Search by name "Target_{unique_id}"
            results = await search_users(
                current_user=searcher_db,
                db=session,
                query=f"Target_{unique_id}",
            )
            assert len(results["items"]) == 1
            assert results["items"][0]["firstName"] == f"Target_{unique_id}"
            assert results["items"][0]["email"] == f"target1_{unique_id}@example.com"

            # Test 2: Search by university name "Kampu_{unique_id}"
            results_uni = await search_users(
                current_user=searcher_db,
                db=session,
                query=f"Kampu_{unique_id}",
            )
            assert len(results_uni["items"]) == 1
            assert results_uni["items"][0]["firstName"] == f"Target_{unique_id}"

            # Test 3: Search by major "CS_{unique_id}"
            results_major = await search_users(
                current_user=searcher_db,
                db=session,
                query=f"CS_{unique_id}",
            )
            assert len(results_major["items"]) == 1
            assert results_major["items"][0]["firstName"] == f"Target_{unique_id}"


    finally:
        await engine.dispose()



