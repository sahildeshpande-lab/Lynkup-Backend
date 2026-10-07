import pytest
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
                 "website": None,
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


def test_university_search_accepts_short_query(monkeypatch) -> None:
    async def _search_short_query(params, _db) -> dict:
        return {
            "query": params.query,
            "items": [],
            "page": params.page,
            "pageSize": params.pageSize,
            "totalItems": 0,
            "totalPages": 0,
        }

    monkeypatch.setattr(search_routes.services, "search_universities", _search_short_query)

    response = client.get("/api/v1/universities", params={"query": "ab", "page": 1, "pageSize": 20})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["data"]["query"] == "ab"


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


def test_university_search_passes_sort_and_order(monkeypatch) -> None:
    captured = {}

    async def _capture_search_universities(params, _db) -> dict:
        captured["sort"] = params.sort
        captured["order"] = params.order
        return {
            "query": params.query,
            "items": [],
            "page": params.page,
            "pageSize": params.pageSize,
            "totalItems": 0,
            "totalPages": 0,
        }

    monkeypatch.setattr(search_routes.services, "search_universities", _capture_search_universities)

    response = client.get(
        "/api/v1/universities",
        params={"query": "kampu", "sort": "created_at", "order": "desc", "page": 1, "pageSize": 20},
    )
    assert response.status_code == 200
    assert captured["sort"] == "created_at"
    assert captured["order"] == "desc"


def test_university_search_omits_default_pagination(monkeypatch) -> None:
    captured = {}

    async def _capture_search_universities(params, _db) -> dict:
        captured["page"] = params.page
        captured["pageSize"] = params.pageSize
        return {
            "query": params.query,
            "items": [],
            "page": 1,
            "pageSize": 1,
            "totalItems": 0,
            "totalPages": 0,
        }

    monkeypatch.setattr(search_routes.services, "search_universities", _capture_search_universities)

    response = client.get("/api/v1/universities", params={"query": "kampu"})
    assert response.status_code == 200
    assert captured["page"] is None
    assert captured["pageSize"] is None


@pytest.mark.asyncio
async def test_search_universities_service_empty_query_execution():
    import uuid
    from unittest.mock import AsyncMock, MagicMock
    from apps.search.schemas import UniversitySearchParams
    from apps.search.services import search_universities
    from apps.profiles.db_models import University

    mock_db = AsyncMock()
    mock_count_res = MagicMock()
    mock_count_res.scalar_one.return_value = 1
    mock_select_res = MagicMock()
    uni = University(name="Test University", slug="test-uni", country_id=uuid.uuid4(), is_active=True)
    mock_select_res.all.return_value = [(uni, "United States")]
    mock_db.execute.side_effect = [mock_count_res, mock_select_res]

    params = UniversitySearchParams(query="", page=1, pageSize=20, order="desc")
    result = await search_universities(params, mock_db)
    assert result["totalItems"] == 1
    assert result["items"][0]["name"] == "Test University"


@pytest.mark.asyncio
async def test_search_universities_service_without_pagination_skips_limit():
    import uuid
    from unittest.mock import AsyncMock, MagicMock
    from apps.search.schemas import UniversitySearchParams
    from apps.search.services import search_universities
    from apps.profiles.db_models import University

    mock_db = AsyncMock()
    mock_count_res = MagicMock()
    mock_count_res.scalar_one.return_value = 1
    mock_select_res = MagicMock()
    uni = University(name="Test University", slug="test-uni", country_id=uuid.uuid4(), is_active=True)
    mock_select_res.all.return_value = [(uni, "United States")]
    mock_db.execute.side_effect = [mock_count_res, mock_select_res]

    params = UniversitySearchParams(query="", order="desc")
    result = await search_universities(params, mock_db)
    select_stmt = mock_db.execute.await_args_list[1].args[0]
    compiled = str(select_stmt.compile(compile_kwargs={"literal_binds": True})).lower()
    assert "limit" not in compiled
    assert "offset" not in compiled
    assert result["page"] == 1
    assert result["pageSize"] == 1




async def _get_academics_info(query, page, page_size, db) -> dict:
    return {
        "educationLevels": [
            {
                "id": "1",
                "name": "Bachelors",
                "interests": [{"id": "5", "name": "Chemistry"}],
            },
            {
                "id": "2",
                "name": "Masters",
                "interests": [{"id": "1", "name": "Artificial Intelligence"}],
            },
        ],
        "countries": {
            "items": [{"id": "1", "name": "India", "iso_code": "IN"}],
            "page": 1,
            "pageSize": 1,
            "totalItems": 1,
            "totalPages": 1,
        },
        "hashtags": {"items": [], "page": 1, "pageSize": 20, "totalItems": 0, "totalPages": 0},
    }


def test_get_academics_info_returns_success(monkeypatch) -> None:
    monkeypatch.setattr(search_routes.services, "get_academics_info", _get_academics_info)

    response = client.get("/api/v1/academicsinfo", params={"query": "Math", "page": 1, "pageSize": 20})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Academics info fetched successfully"
    assert "interests" not in body["data"]
    assert "educationLevels" in body["data"]
    assert body["data"]["educationLevels"][0] == {
        "id": "1",
        "name": "Bachelors",
        "interests": [{"id": "5", "name": "Chemistry"}],
    }
    assert body["data"]["educationLevels"][1]["interests"][0]["name"] == "Artificial Intelligence"
    assert body["data"]["countries"]["items"][0] == {"id": "1", "name": "India", "iso_code": "IN"}
    assert body["data"]["countries"]["totalItems"] == 1


def test_list_countries_returns_success(monkeypatch) -> None:
    async def _list_countries(query, page, page_size, db, sort=None, order=None, *args, **kwargs) -> dict:
        assert query == "ind"
        assert page == 1
        assert page_size == 20
        return {
            "items": [{"id": "1", "name": "India", "iso_code": "IN"}],
            "page": 1,
            "pageSize": 20,
            "totalItems": 1,
            "totalPages": 1,
        }

    monkeypatch.setattr(search_routes.services, "list_countries", _list_countries)



    response = client.get("/api/v1/countries", params={"query": "ind", "page": 1, "pageSize": 20})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Countries fetched successfully"
    assert body["data"]["items"] == [{"id": "1", "name": "India", "iso_code": "IN"}]
    assert body["data"]["totalItems"] == 1


def test_list_countries_sorts_by_created_at(monkeypatch) -> None:
    captured: dict = {}

    async def _list_countries(**kwargs) -> dict:
        captured.update(kwargs)
        return {
            "items": [],
            "page": 1,
            "pageSize": 20,
            "totalItems": 0,
            "totalPages": 0,
        }

    monkeypatch.setattr(search_routes.services, "list_countries", _list_countries)

    response = client.get(
        "/api/v1/countries",
        params={"sort": "created_at", "order": "desc"},
    )

    assert response.status_code == 200
    assert captured["sort"] == "created_at"
    assert captured["order"] == "desc"

    captured.clear()
    response = client.get(
        "/api/v1/countries",
        params={"sort": "created_at", "order": "asc"},
    )
    assert response.status_code == 200
    assert captured["sort"] == "created_at"
    assert captured["order"] == "asc"


def test_list_majors_returns_title_cased_combined_values(monkeypatch) -> None:
    async def _list_majors(query, page, page_size, db) -> dict:
        assert query == "a"
        assert page == 1
        assert page_size == 20
        return {
            "items": [{"name": "Ai"}, {"name": "Applied Math"}],
            "page": 1,
            "pageSize": 20,
            "totalItems": 2,
            "totalPages": 1,
        }

    monkeypatch.setattr(search_routes.services, "list_majors", _list_majors)

    response = client.get("/api/v1/majors", params={"query": "a", "page": 1, "pageSize": 20})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Majors fetched successfully"
    assert body["data"]["items"] == [{"name": "Ai"}, {"name": "Applied Math"}]


def test_list_minors_returns_title_cased_combined_values(monkeypatch) -> None:
    async def _list_minors(query, page, page_size, db) -> dict:
        return {
            "items": [{"name": "Ai"}],
            "page": 1,
            "pageSize": 1,
            "totalItems": 1,
            "totalPages": 1,
        }

    monkeypatch.setattr(search_routes.services, "list_minors", _list_minors)

    response = client.get("/api/v1/minor", params={"page": 1, "pageSize": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Minors fetched successfully"
    assert body["data"]["items"] == [{"name": "Ai"}]


def test_create_academic_interest_returns_created(monkeypatch) -> None:
    async def _create_academic_interest(name, education_level_id, db) -> dict:
        assert name == "Data Science"
        assert education_level_id == 1
        return {
            "id": "7",
            "name": name,
            "educationLevelId": str(education_level_id),
            "isActive": True,
        }

    monkeypatch.setattr(
        search_routes.services,
        "create_academic_interest",
        _create_academic_interest,
    )

    response = client.post(
        "/api/v1/academic-interests",
        json={"name": "  Data   Science  ", "educationLevelId": 1},
    )

    assert response.status_code == 201
    assert response.json() == {
        "status": True,
        "message": "Academic interest created successfully",
        "data": {
            "id": "7",
            "name": "Data Science",
            "educationLevelId": "1",
            "isActive": True,
        },
    }


def test_search_users_route(monkeypatch) -> None:
    async def _mock_search_users(
        current_user,
        db,
        query,
        page,
        page_size,
        university_name=None,
    ):
        assert university_name == ["Kampu University|State University"]
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
        "/api/v1/search-user",
        params={
            "query": "John Kampu CS Math",
            "university_name": "Kampu University|State University",
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


def test_search_posts_route_forwards_repeated_filters_and_to_all(monkeypatch) -> None:
    captured = {}

    async def _mock_search_posts(current_user, db, **kwargs):
        captured.update(kwargs)
        return {
            "items": [],
            "page": 1,
            "pageSize": 20,
            "totalItems": 0,
            "totalPages": 0,
        }

    monkeypatch.setattr(search_routes.services, "search_posts", _mock_search_posts)

    response = client.get(
        "/api/v1/search/posts",
        params=[
            ("hashtag", "python"),
            ("hashtag", "fastapi"),
            ("hashtag_to_all", "true"),
            ("academic_interest", "AI"),
            ("academic_interest", "ML"),
            ("academic_interest_to_all", "true"),
            ("university_name", "123"),
            ("university_name", "456"),
            ("university_to_all", "true"),
            ("major", "Computer Science"),
            ("major", "IT"),
            ("major_to_all", "true"),
            ("minor", "AI"),
            ("minor", "Data Science"),
            ("minor_to_all", "true"),
            ("country", "IN"),
            ("country_to_all", "true"),
            ("edu_level", "Bachelors"),
            ("edu_level_to_all", "true"),
            ("page", "1"),
            ("pageSize", "20"),
        ],
    )

    assert response.status_code == 200
    assert captured["hashtag"] == ["python", "fastapi"]
    assert captured["hashtag_to_all"] is True
    assert captured["academic_interest"] == ["AI", "ML"]
    assert captured["academic_interest_to_all"] is True
    assert captured["university_name"] == ["123", "456"]
    assert captured["university_to_all"] is True
    assert captured["major"] == ["Computer Science", "IT"]
    assert captured["major_to_all"] is True
    assert captured["minor"] == ["AI", "Data Science"]
    assert captured["minor_to_all"] is True
    assert captured["country"] == ["IN"]
    assert captured["country_to_all"] is True
    assert captured["edu_level"] == ["Bachelors"]
    assert captured["edu_level_to_all"] is True
    assert captured["page"] == 1
    assert captured["page_size"] == 20


def test_search_posts_route_omitted_to_all_defaults_false(monkeypatch) -> None:
    captured = {}

    async def _mock_search_posts(current_user, db, **kwargs):
        captured.update(kwargs)
        return {
            "items": [],
            "page": 1,
            "pageSize": 20,
            "totalItems": 0,
            "totalPages": 0,
        }

    monkeypatch.setattr(search_routes.services, "search_posts", _mock_search_posts)

    response = client.get(
        "/api/v1/search/posts",
        params=[
            ("hashtag", "python"),
            ("hashtag", "fastapi"),
            ("major", "Computer Science"),
            ("minor", "AI"),
        ],
    )

    assert response.status_code == 200
    assert captured["hashtag"] == ["python", "fastapi"]
    assert captured["major"] == ["Computer Science"]
    assert captured["minor"] == ["AI"]
    assert captured["hashtag_to_all"] is False
    assert captured["academic_interest_to_all"] is False
    assert captured["university_to_all"] is False
    assert captured["major_to_all"] is False
    assert captured["minor_to_all"] is False
    assert captured["country_to_all"] is False
    assert captured["edu_level_to_all"] is False


def test_search_posts_openapi_to_all_params_follow_filters() -> None:
    params = app.openapi()["paths"]["/api/v1/search/posts"]["get"]["parameters"]
    names = [param["name"] for param in params]
    pairs = [
        ("hashtag", "hashtag_to_all"),
        ("academic_interest", "academic_interest_to_all"),
        ("university_name", "university_to_all"),
        ("major", "major_to_all"),
        ("minor", "minor_to_all"),
        ("country", "country_to_all"),
        ("edu_level", "edu_level_to_all"),
    ]
    for filter_name, to_all_name in pairs:
        assert names.index(to_all_name) == names.index(filter_name) + 1
        to_all_param = next(param for param in params if param["name"] == to_all_name)
        description = to_all_param["description"].lower()
        assert "false" in description or "omitted" in description
        assert "true" in description
        assert to_all_param["schema"].get("default") is False
    assert names.index("page") > names.index("edu_level_to_all")
    assert "pageSize" in names
    major_param = next(param for param in params if param["name"] == "major")
    minor_param = next(param for param in params if param["name"] == "minor")

    def _is_array_schema(schema: dict) -> bool:
        if schema.get("type") == "array":
            return True
        return any(
            option.get("type") == "array"
            for option in schema.get("anyOf", []) + schema.get("oneOf", [])
        )

    assert _is_array_schema(major_param["schema"])
    assert _is_array_schema(minor_param["schema"])


def test_viewer_role_access_to_catalog_endpoints(monkeypatch) -> None:
    async def _override_viewer_user():
        return User(email="viewer@example.com", role="viewer", firebase_uid="viewer-uid")

    app.dependency_overrides[get_current_user] = _override_viewer_user

    async def _dummy_list_countries(*args, **kwargs):
        return {"items": [{"id": "c-1", "name": "India", "iso_code": "IN"}], "page": 1, "pageSize": 10, "totalItems": 1, "totalPages": 1}

    async def _dummy_search_universities(*args, **kwargs):
        return {"items": [{"id": "u-1", "name": "Stanford University"}], "page": 1, "pageSize": 10, "totalItems": 1, "totalPages": 1}

    async def _dummy_list_majors(*args, **kwargs):
        return {"items": ["Computer Science"], "page": 1, "pageSize": 10, "totalItems": 1, "totalPages": 1}

    async def _dummy_list_minors(*args, **kwargs):
        return {"items": ["Mathematics"], "page": 1, "pageSize": 10, "totalItems": 1, "totalPages": 1}

    async def _dummy_list_interests(*args, **kwargs):
        return {"items": [{"id": 1, "name": "Artificial Intelligence"}], "page": 1, "pageSize": 10, "totalItems": 1, "totalPages": 1}

    monkeypatch.setattr(search_routes.services, "list_countries", _dummy_list_countries)
    monkeypatch.setattr(search_routes.services, "search_universities", _dummy_search_universities)
    monkeypatch.setattr(search_routes.services, "list_majors", _dummy_list_majors)
    monkeypatch.setattr(search_routes.services, "list_minors", _dummy_list_minors)
    monkeypatch.setattr(search_routes.academic_catalog_services, "list_test_interests", _dummy_list_interests)

    # 1] /countries
    res_countries = client.get("/api/v1/countries")
    assert res_countries.status_code == 200
    assert res_countries.json()["status"] is True

    # 2] /universities
    res_unis = client.get("/api/v1/universities", params={"query": "stanford"})
    assert res_unis.status_code == 200
    assert res_unis.json()["status"] is True

    # 3] /majors
    res_majors = client.get("/api/v1/majors")
    assert res_majors.status_code == 200
    assert res_majors.json()["status"] is True

    # 4] /minor
    res_minor = client.get("/api/v1/minor")
    assert res_minor.status_code == 200
    assert res_minor.json()["status"] is True

    # 5] /interest
    res_interest = client.get("/api/v1/interest")
    assert res_interest.status_code == 200
    assert res_interest.json()["status"] is True

    app.dependency_overrides[get_current_user] = _override_current_user


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
from apps.connections.db_models.follow import Follow
from apps.connections.db_models.connection_request import ConnectionRequest


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

            # Assign roles (create role rows if this DB has none yet)
            user_role_obj = (await session.execute(select(Role).where(Role.name == "user"))).scalar_one_or_none()
            if user_role_obj is None:
                user_role_obj = Role(name="user", description="user role")
                session.add(user_role_obj)
                await session.flush()
            mod_role_obj = (await session.execute(select(Role).where(Role.name == "moderator"))).scalar_one_or_none()
            if mod_role_obj is None:
                mod_role_obj = Role(name="moderator", description="moderator role")
                session.add(mod_role_obj)
                await session.flush()

            session.add(UserRole(user_id=searcher.id, role_id=user_role_obj.id))
            session.add(UserRole(user_id=target1.id, role_id=user_role_obj.id))
            session.add(UserRole(user_id=mod.id, role_id=mod_role_obj.id))
            session.add(UserRole(user_id=inactive_target.id, role_id=user_role_obj.id))
            await session.commit()


            # Create profiles
            country_res = await session.execute(select(Country).where(Country.iso_code == "US"))
            country = country_res.scalars().first()
            if not country:
                country = Country(name="United States", iso_code="US")
                session.add(country)
                await session.flush()

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
            session.add(Block(blocker_user_id=searcher.id, blocked_user_id=target1.id, is_active=True))
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
            assert results["items"][0]["is_deleted"] is False
            assert results["items"][0]["is_connected"] is False
            assert results["items"][0]["is_followed"] is False
            assert results["items"][0]["is_blocked"] is True
            assert results["items"][0]["request_sent"] is False
            assert results["items"][0]["request_received"] is False

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


@pytest.mark.asyncio
async def test_get_relationship_flags_logic(monkeypatch) -> None:
    # Mock file_exists to return True
    monkeypatch.setattr("core.images.file_exists", lambda key: True)
    # Mock normalize_image_name
    monkeypatch.setattr("core.images.normalize_image_name", lambda key: f"normalized/{key}")

    try:
        await init_db()
        async with async_session_factory() as session:
            import uuid
            unique_id = str(uuid.uuid4())[:8]

            # 1. Create a searcher and targets
            searcher = User(email=f"s_{unique_id}@example.com", role="user", firebase_uid=f"uid_s_{unique_id}")
            t1 = User(email=f"t1_{unique_id}@example.com", role="user", firebase_uid=f"uid_t1_{unique_id}", status="active")
            t2 = User(email=f"t2_{unique_id}@example.com", role="user", firebase_uid=f"uid_t2_{unique_id}", status="active")
            t3 = User(email=f"t3_{unique_id}@example.com", role="user", firebase_uid=f"uid_t3_{unique_id}", status="active")
            t4 = User(email=f"t4_{unique_id}@example.com", role="user", firebase_uid=f"uid_t4_{unique_id}", status="active")
            t5 = User(email=f"t5_{unique_id}@example.com", role="user", firebase_uid=f"uid_t5_{unique_id}", status="active")

            session.add(searcher)
            session.add(t1)
            session.add(t2)
            session.add(t3)
            session.add(t4)
            session.add(t5)
            await session.commit()
            
            await session.refresh(searcher)
            await session.refresh(t1)
            await session.refresh(t2)
            await session.refresh(t3)
            await session.refresh(t4)
            await session.refresh(t5)

            from apps.connections.services.connection_service import build_connection_pair
            low_id, high_id = build_connection_pair(searcher.id, t1.id)
            conn = Connection(user_low_id=low_id, user_high_id=high_id, is_active=True)
            session.add(conn)

            follow = Follow(follower_user_id=searcher.id, following_user_id=t2.id, is_active=True)
            session.add(follow)

            block = Block(blocker_user_id=searcher.id, blocked_user_id=t3.id, is_active=True)
            session.add(block)

            req_sent = ConnectionRequest(sender_user_id=searcher.id, receiver_user_id=t4.id, status="pending")
            session.add(req_sent)

            req_rcvd = ConnectionRequest(sender_user_id=t5.id, receiver_user_id=searcher.id, status="pending")
            session.add(req_rcvd)

            await session.commit()

        # Run get_relationship_flags
        async with async_session_factory() as session:
            from apps.connections.services import get_relationship_flags
            flags = await get_relationship_flags(
                session,
                searcher.id,
                [t1.id, t2.id, t3.id, t4.id, t5.id]
            )

            # Verify t1 (connected)
            assert flags[t1.id]["is_connected"] is True
            assert flags[t1.id]["is_followed"] is False
            assert flags[t1.id]["is_blocked"] is False
            assert flags[t1.id]["request_sent"] is False
            assert flags[t1.id]["request_received"] is False

            # Verify t2 (followed)
            assert flags[t2.id]["is_connected"] is False
            assert flags[t2.id]["is_followed"] is True
            assert flags[t2.id]["is_blocked"] is False
            assert flags[t2.id]["request_sent"] is False
            assert flags[t2.id]["request_received"] is False

            # Verify t3 (blocked)
            assert flags[t3.id]["is_connected"] is False
            assert flags[t3.id]["is_followed"] is False
            assert flags[t3.id]["is_blocked"] is True
            assert flags[t3.id]["request_sent"] is False
            assert flags[t3.id]["request_received"] is False

            # Verify t4 (request_sent)
            assert flags[t4.id]["is_connected"] is False
            assert flags[t4.id]["is_followed"] is False
            assert flags[t4.id]["is_blocked"] is False
            assert flags[t4.id]["request_sent"] is True
            assert flags[t4.id]["request_received"] is False

            # Verify t5 (request_received)
            assert flags[t5.id]["is_connected"] is False
            assert flags[t5.id]["is_followed"] is False
            assert flags[t5.id]["is_blocked"] is False
            assert flags[t5.id]["request_sent"] is False
            assert flags[t5.id]["request_received"] is True

    finally:
        await engine.dispose()




