"""Tests for GET /api/v1/admin/learning-spotlight/users."""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from apps.accounts.db_models import User
from apps.administration import routes as admin_routes
from apps.administration.services import user_management_service as ums
from apps.learningspotlight.routes import router as spotlight_router
from apps.learningspotlight.schemas import LearningSpotlightAdminUserItem
from apps.profiles.db_models.profile_db_model import Profile
from common.enums import ReportEntityType, UserStatus
from common.exceptions import ApiError
from core.database.session import get_session
from core.security.auth import get_current_admin
from apps.administration.dependencies import require_signed_admin


EXISTING_USER_FIELDS = [
    "id",
    "firstName",
    "lastName",
    "email",
    "role",
    "loginType",
    "profilePhoto_url",
    "bannerPhotoUrl",
    "status",
    "university",
    "university_details",
    "major",
    "major_details",
    "minor",
    "minor_details",
    "country",
    "country_details",
    "educationLevel",
    "educationLevel_details",
    "bio",
    "academicInterests",
    "academicInterests_details",
    "graduationDate",
    "is_graduation_completed",
    "is_alumni",
    "location",
    "profileVisibility",
    "completenessScore",
    "notificationPreferences",
    "isEmailVerified",
    "email_verified_at",
    "has_changed_email_after_graduation",
    "onlinePresence",
    "posts_count",
    "followers_count",
    "following_count",
    "connection_count",
    "createdAt",
    "updatedAt",
    "is_onboarding_completed",
    "is_deleted",
    "moderation_notes",
]

SPOTLIGHT_FIELDS = [
    "user_id",
    "extracted_keywords",
    "is_learning_spotlight_recommended",
    "learning_spotlight_recommended_at",
    "recommended_cycle_name",
    "paper_id",
    "paper_title",
]


def _complete_user_payload(user_id, *, first_name="Esha", email="esha@yopmail.com") -> dict:
    return {
        "id": str(user_id),
        "firstName": first_name,
        "lastName": "Wagh",
        "email": email,
        "role": "user",
        "loginType": "email",
        "profilePhoto_url": None,
        "bannerPhotoUrl": None,
        "status": "Active",
        "university": "Air University",
        "university_details": {
            "id": str(uuid4()),
            "university_name": "Air University",
            "university_website": "http://www.au.edu.pk/",
        },
        "major": "Accounting",
        "major_details": {"id": 1, "major_name": "Accounting"},
        "minor": "Accounting and Finance",
        "minor_details": {"id": 15, "minor_name": "Accounting and Finance"},
        "country": str(uuid4()),
        "country_details": {"id": str(uuid4()), "country_name": "India"},
        "educationLevel": "Bachelors",
        "educationLevel_details": {"id": 1, "edu_level": "Bachelors"},
        "bio": "User",
        "academicInterests": ["Accounting Standards & IFRS", "Risk Management"],
        "academicInterests_details": [],
        "graduationDate": "18-09-2026",
        "is_graduation_completed": False,
        "is_alumni": False,
        "location": None,
        "profileVisibility": "private",
        "completenessScore": 86,
        "notificationPreferences": {"email": True, "push": True, "inApp": True},
        "isEmailVerified": True,
        "email_verified_at": "2026-09-17T13:05:18.932799+00:00",
        "has_changed_email_after_graduation": False,
        "onlinePresence": True,
        "posts_count": 1,
        "followers_count": 0,
        "following_count": 0,
        "connection_count": 1,
        "createdAt": "2026-09-17T13:05:02.277353+00:00",
        "updatedAt": "2026-09-17T13:05:18.932799+00:00",
        "is_onboarding_completed": True,
        "is_deleted": False,
        "moderation_notes": None,
    }


def _compile(stmt) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True})).lower()


def _make_user_and_profile(
    *,
    first_name: str = "Esha",
    email: str | None = None,
    learning_spotlight: dict | None = None,
    extracted_keywords: dict | None = None,
    recommended_at: datetime | None = None,
):
    user_id = uuid4()
    user = User(
        id=user_id,
        email=email or f"user_{user_id}@example.com",
        firebase_uid=f"fb-{user_id}",
        status=UserStatus.active,
    )
    profile = Profile(
        user_id=user_id,
        first_name=first_name,
        last_name="Wagh",
        extracted_keywords=extracted_keywords,
        learning_spotlight=learning_spotlight,
        learning_spotlight_updated_at=recommended_at,
    )
    return user, profile


class _Row:
    def __init__(self, user, profile, **extra):
        self.User = user
        self.Profile = profile
        self.university_name = None
        self.country_name = None
        for key, value in extra.items():
            setattr(self, key, value)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


def _build_route_app(*, admin=None, db=None):
    app = FastAPI()
    app.include_router(spotlight_router, prefix="/api/v1")

    @app.exception_handler(ApiError)
    async def api_error_handler(_request, exc: ApiError):
        message = str(exc.message) if hasattr(exc, "message") else str(exc)
        status_code = 403 if "Insufficient permissions" in message else 401
        return JSONResponse(
            status_code=status_code,
            content={"status": False, "message": message, "data": None},
        )

    if admin is not None:
        app.dependency_overrides[get_current_admin] = lambda: admin
        app.dependency_overrides[require_signed_admin] = lambda: admin
    if db is not None:
        async def _override_db():
            yield db
        app.dependency_overrides[get_session] = _override_db
    else:
        async def _override_db():
            yield AsyncMock()
        app.dependency_overrides[get_session] = _override_db
    return app


# ---------------------------------------------------------------------------
# Clause / schema
# ---------------------------------------------------------------------------


def test_learning_spotlight_clause_true_filters_recommended_today() -> None:
    compiled = _compile(ums._build_learning_spotlight_clause(True))
    assert "learning_spotlight_updated_at" in compiled
    assert "is not null" in compiled


def test_learning_spotlight_clause_false_excludes_recommended_today() -> None:
    compiled = _compile(ums._build_learning_spotlight_clause(False))
    assert "learning_spotlight_updated_at" in compiled
    assert "not" in compiled


def test_learning_spotlight_clause_omitted_is_noop() -> None:
    assert ums._build_learning_spotlight_clause(None) is True


def test_learning_spotlight_admin_user_schema_accepts_existing_user_fields() -> None:
    user_id = uuid4()
    payload = _complete_user_payload(user_id)
    payload.update(
        {
            "user_id": str(user_id),
            "extracted_keywords": {"major": ["Accounting"]},
            "is_learning_spotlight_recommended": True,
            "learning_spotlight_recommended_at": "2026-09-17T13:05:18.932799+00:00",
            "recommended_cycle_name": "Leading Thinker",
            "paper_id": ["003e3a6be8537162fd112b3a0a51a6063b640997", "paper-2"],
            "paper_title": [
                "Noether Symmetries and Covariant Conservation Laws in Classical, Relativistic and Quantum Physics",
                "Paper Two Title",
            ],
        }
    )
    item = LearningSpotlightAdminUserItem.model_validate(payload)
    assert item.user_id == str(user_id)
    assert item.is_learning_spotlight_recommended is True
    assert item.extracted_keywords["major"] == ["Accounting"]
    assert item.recommended_cycle_name == "Leading Thinker"
    assert item.paper_id == [
        "003e3a6be8537162fd112b3a0a51a6063b640997",
        "paper-2",
    ]
    assert item.paper_title[0].startswith("Noether Symmetries")
    assert item.firstName == "Esha"


def test_existing_users_route_signature_is_unchanged() -> None:
    params = inspect.signature(admin_routes.list_users).parameters
    assert "is_learning_spotlight_recommended" not in params
    assert "search" in params
    assert "page" in params
    assert "pageSize" in params


# ---------------------------------------------------------------------------
# Service: field mapping + existing user object
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_attaches_complete_user_and_spotlight_fields() -> None:
    recommended_at = datetime.now(timezone.utc)
    keywords = {
        "major": ["Accounting"],
        "minor": ["Accounting and Finance"],
        "hashtags": {},
        "interests": [],
        "content_keywords": {},
        "engagement_keywords": {},
    }
    user, profile = _make_user_and_profile(
        learning_spotlight={
            "version": 2,
            "spotlight_type": "leading_thinker",
            "papers": [
                {
                    "paper_id": "003e3a6be8537162fd112b3a0a51a6063b640997",
                    "title": (
                        "Noether Symmetries and Covariant Conservation Laws in "
                        "Classical, Relativistic and Quantum Physics"
                    ),
                },
                {"paper_id": "paper-2", "title": "Paper Two Title"},
            ],
        },
        extracted_keywords=keywords,
        recommended_at=recommended_at,
    )
    row = _Row(
        user,
        profile,
        spotlight_user_id=profile.user_id,
        extracted_keywords=keywords,
        is_learning_spotlight_recommended=True,
        learning_spotlight_recommended_at=recommended_at,
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Result([row]))
    base = _complete_user_payload(user.id)

    with (
        patch.object(ums, "build_user_base_response", AsyncMock(return_value=dict(base))),
        patch(
            "apps.moderation.repositories.get_latest_comments_by_entity_ids",
            AsyncMock(return_value={user.id: None}),
        ),
    ):
        items = await ums._fetch_users_with_details(
            db,
            page=1,
            page_size=10,
            role="user",
            include_learning_spotlight_fields=True,
        )

    item = items[0]
    for field in EXISTING_USER_FIELDS:
        assert field in item, field
    for field in SPOTLIGHT_FIELDS:
        assert field in item, field
    assert item["user_id"] == str(user.id)
    assert item["extracted_keywords"] == keywords
    assert item["is_learning_spotlight_recommended"] is True
    assert item["learning_spotlight_recommended_at"] == recommended_at.isoformat()
    assert item["recommended_cycle_name"] == "Leading Thinker"
    assert item["paper_id"] == [
        "003e3a6be8537162fd112b3a0a51a6063b640997",
        "paper-2",
    ]
    assert item["paper_title"] == [
        "Noether Symmetries and Covariant Conservation Laws in Classical, Relativistic and Quantum Physics",
        "Paper Two Title",
    ]
    assert item["firstName"] == "Esha"
    assert item["email"] == base["email"]


@pytest.mark.asyncio
async def test_fetch_recommended_false_when_learning_spotlight_is_null() -> None:
    user, profile = _make_user_and_profile(learning_spotlight=None, recommended_at=None)
    row = _Row(
        user,
        profile,
        spotlight_user_id=profile.user_id,
        extracted_keywords=None,
        is_learning_spotlight_recommended=False,
        learning_spotlight_recommended_at=None,
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Result([row]))

    with (
        patch.object(
            ums,
            "build_user_base_response",
            AsyncMock(return_value={"id": str(user.id), "email": user.email}),
        ),
        patch(
            "apps.moderation.repositories.get_latest_comments_by_entity_ids",
            AsyncMock(return_value={}),
        ),
    ):
        items = await ums._fetch_users_with_details(
            db,
            page=1,
            page_size=10,
            role="user",
            include_learning_spotlight_fields=True,
        )

    assert items[0]["is_learning_spotlight_recommended"] is False
    assert items[0]["learning_spotlight_recommended_at"] is None
    assert items[0]["recommended_cycle_name"] is None
    assert items[0]["paper_id"] == []
    assert items[0]["paper_title"] == []
    assert items[0]["user_id"] == str(user.id)


@pytest.mark.asyncio
async def test_fetch_does_not_attach_spotlight_fields_for_existing_users_list() -> None:
    user, profile = _make_user_and_profile(
        learning_spotlight={"papers": []},
        extracted_keywords={"major": ["AI"]},
    )
    row = _Row(user, profile)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Result([row]))

    with (
        patch.object(
            ums,
            "build_user_base_response",
            AsyncMock(return_value={"id": str(user.id), "email": user.email}),
        ),
        patch(
            "apps.moderation.repositories.get_latest_comments_by_entity_ids",
            AsyncMock(return_value={}),
        ),
    ):
        items = await ums._fetch_users_with_details(
            db, page=1, page_size=10, role="user"
        )

    assert "is_learning_spotlight_recommended" not in items[0]
    assert "extracted_keywords" not in items[0]
    assert "learning_spotlight_recommended_at" not in items[0]
    assert "user_id" not in items[0]


@pytest.mark.asyncio
async def test_fetch_falls_back_to_profile_when_row_labels_missing() -> None:
    recommended_at = datetime.now(timezone.utc)
    user, profile = _make_user_and_profile(
        learning_spotlight={"version": 2, "spotlight_type": "country_perspective"},
        extracted_keywords={"major": ["Accounting"]},
        recommended_at=recommended_at,
    )
    row = _Row(user, profile)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Result([row]))

    with (
        patch.object(
            ums,
            "build_user_base_response",
            AsyncMock(return_value={"id": str(user.id)}),
        ),
        patch(
            "apps.moderation.repositories.get_latest_comments_by_entity_ids",
            AsyncMock(return_value={}),
        ),
    ):
        items = await ums._fetch_users_with_details(
            db,
            page=1,
            page_size=10,
            role="user",
            include_learning_spotlight_fields=True,
        )

    assert items[0]["is_learning_spotlight_recommended"] is True
    assert items[0]["learning_spotlight_recommended_at"] == recommended_at.isoformat()
    assert items[0]["extracted_keywords"] == {"major": ["Accounting"]}
    assert items[0]["recommended_cycle_name"] == "Country Perspective"


@pytest.mark.asyncio
async def test_fetch_recommended_false_when_recommended_on_prior_day() -> None:
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    user, profile = _make_user_and_profile(
        learning_spotlight={"version": 2, "spotlight_type": "leading_thinker"},
        extracted_keywords={"major": ["Accounting"]},
        recommended_at=yesterday,
    )
    row = _Row(user, profile)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Result([row]))

    with (
        patch.object(
            ums,
            "build_user_base_response",
            AsyncMock(return_value={"id": str(user.id)}),
        ),
        patch(
            "apps.moderation.repositories.get_latest_comments_by_entity_ids",
            AsyncMock(return_value={}),
        ),
    ):
        items = await ums._fetch_users_with_details(
            db,
            page=1,
            page_size=10,
            role="user",
            include_learning_spotlight_fields=True,
        )

    assert items[0]["is_learning_spotlight_recommended"] is False
    assert items[0]["learning_spotlight_recommended_at"] == yesterday.isoformat()
    assert items[0]["recommended_cycle_name"] == "Leading Thinker"


# ---------------------------------------------------------------------------
# Service: filters, search, pagination, totals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_query_includes_spotlight_columns_and_true_filter() -> None:
    captured = []

    class _Empty:
        def all(self):
            return []

    db = AsyncMock()

    async def _execute(stmt, *args, **kwargs):
        captured.append(stmt)
        return _Empty()

    db.execute = AsyncMock(side_effect=_execute)

    with patch(
        "apps.moderation.repositories.get_latest_comments_by_entity_ids",
        AsyncMock(return_value={}),
    ):
        await ums._fetch_users_with_details(
            db,
            page=2,
            page_size=10,
            role="user",
            search="Prasad",
            is_learning_spotlight_recommended=True,
            include_learning_spotlight_fields=True,
        )

    compiled = _compile(captured[0])
    assert "extracted_keywords" in compiled
    assert "learning_spotlight_updated_at" in compiled
    assert "prasad" in compiled
    assert "limit" in compiled
    assert "offset" in compiled


@pytest.mark.asyncio
async def test_fetch_query_false_filter_excludes_recommended_today() -> None:
    captured = []

    class _Empty:
        def all(self):
            return []

    db = AsyncMock()

    async def _execute(stmt, *args, **kwargs):
        captured.append(stmt)
        return _Empty()

    db.execute = AsyncMock(side_effect=_execute)

    with patch(
        "apps.moderation.repositories.get_latest_comments_by_entity_ids",
        AsyncMock(return_value={}),
    ):
        await ums._fetch_users_with_details(
            db,
            page=1,
            page_size=10,
            role="user",
            is_learning_spotlight_recommended=False,
            include_learning_spotlight_fields=True,
        )

    compiled = _compile(captured[0])
    assert "learning_spotlight_updated_at" in compiled
    assert "not" in compiled


@pytest.mark.asyncio
async def test_fetch_query_omits_spotlight_filter_when_unset() -> None:
    captured = []

    class _Empty:
        def all(self):
            return []

    db = AsyncMock()

    async def _execute(stmt, *args, **kwargs):
        captured.append(stmt)
        return _Empty()

    db.execute = AsyncMock(side_effect=_execute)

    with patch(
        "apps.moderation.repositories.get_latest_comments_by_entity_ids",
        AsyncMock(return_value={}),
    ):
        await ums._fetch_users_with_details(db, page=1, page_size=10, role="user")

    compiled = _compile(captured[0])
    where_sql = compiled.split(" where ", 1)[-1] if " where " in compiled else compiled
    assert "learning_spotlight_updated_at >=" not in where_sql
    assert "learning_spotlight is not null" not in where_sql
    assert "learning_spotlight is null" not in where_sql


@pytest.mark.asyncio
async def test_list_learning_spotlight_users_passes_filter_and_fields_flag() -> None:
    captured: dict[str, object] = {}

    async def _fake_fetch(_db, page=None, page_size=None, search=None, role=None, **kwargs):
        captured.update(kwargs)
        captured["page"] = page
        captured["page_size"] = page_size
        captured["search"] = search
        captured["role"] = role
        return [{"id": "1", "is_learning_spotlight_recommended": True}]

    class _Scalar:
        def scalar_one(self):
            return 4

    class _Session:
        async def execute(self, stmt):
            captured["count_sql"] = _compile(stmt)
            return _Scalar()

    with patch.object(ums, "_fetch_users_with_details", _fake_fetch):
        result = await ums.list_learning_spotlight_users(
            page=1,
            page_size=10,
            db=_Session(),
            search="Prasad",
            status="Active",
            is_learning_spotlight_recommended=True,
        )

    assert captured["include_learning_spotlight_fields"] is True
    assert captured["is_learning_spotlight_recommended"] is True
    assert captured["status"] == "Active"
    assert captured["search"] == "Prasad"
    assert captured["page"] == 1
    assert captured["page_size"] == 10
    assert captured["role"] == "user"
    assert "learning_spotlight_updated_at" in captured["count_sql"]
    assert "prasad" in captured["count_sql"]
    assert result["totalItems"] == 4
    assert result["page"] == 1
    assert result["pageSize"] == 10
    assert result["items"][0]["is_learning_spotlight_recommended"] is True


@pytest.mark.asyncio
async def test_list_learning_spotlight_users_false_filter_count_sql() -> None:
    captured: dict[str, object] = {}

    async def _fake_fetch(_db, *args, **kwargs):
        return []

    class _Scalar:
        def scalar_one(self):
            return 2

    class _Session:
        async def execute(self, stmt):
            captured["count_sql"] = _compile(stmt)
            return _Scalar()

    with patch.object(ums, "_fetch_users_with_details", _fake_fetch):
        result = await ums.list_learning_spotlight_users(
            page=1,
            page_size=10,
            db=_Session(),
            is_learning_spotlight_recommended=False,
        )

    assert "learning_spotlight_updated_at" in captured["count_sql"]
    assert "not" in captured["count_sql"]
    assert result["totalItems"] == 2


@pytest.mark.asyncio
async def test_list_learning_spotlight_users_no_filter_count_omits_spotlight() -> None:
    captured: dict[str, object] = {}

    async def _fake_fetch(_db, *args, **kwargs):
        captured["kwargs"] = kwargs
        return [
            {"id": "rec", "is_learning_spotlight_recommended": True},
            {"id": "plain", "is_learning_spotlight_recommended": False},
        ]

    class _Scalar:
        def scalar_one(self):
            return 2

    class _Session:
        async def execute(self, stmt):
            captured["count_sql"] = _compile(stmt)
            return _Scalar()

    with patch.object(ums, "_fetch_users_with_details", _fake_fetch):
        result = await ums.list_learning_spotlight_users(
            page=1, page_size=10, db=_Session()
        )

    assert captured["kwargs"]["is_learning_spotlight_recommended"] is None
    assert "learning_spotlight_updated_at" not in captured["count_sql"]
    flags = [item["is_learning_spotlight_recommended"] for item in result["items"]]
    assert True in flags and False in flags
    assert result["totalItems"] == 2


@pytest.mark.asyncio
async def test_existing_list_users_does_not_enable_spotlight_fields() -> None:
    captured: dict[str, object] = {}

    async def _fake_fetch(_db, *args, **kwargs):
        captured.update(kwargs)
        return [{"id": "1", "email": "user@example.com"}]

    class _Scalar:
        def scalar_one(self):
            return 1

    class _Session:
        async def execute(self, stmt):
            captured["count_sql"] = _compile(stmt)
            return _Scalar()

    with patch.object(ums, "_fetch_users_with_details", _fake_fetch):
        result = await ums.list_users(page=1, page_size=10, db=_Session())

    assert captured.get("include_learning_spotlight_fields") is False
    assert captured.get("is_learning_spotlight_recommended") is None
    assert "learning_spotlight" not in captured["count_sql"]
    assert "is_learning_spotlight_recommended" not in result["items"][0]


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_learning_spotlight_users_endpoint_returns_200() -> None:
    admin = User(id=uuid4(), email="admin@example.com", role="superadmin")
    user_id = uuid4()
    item = _complete_user_payload(user_id)
    item.update(
        {
            "user_id": str(user_id),
            "extracted_keywords": {"major": ["Accounting"]},
            "is_learning_spotlight_recommended": True,
            "learning_spotlight_recommended_at": "2026-09-17T10:22:51.588000+00:00",
            "recommended_cycle_name": "Leading Thinker",
            "paper_id": ["003e3a6be8537162fd112b3a0a51a6063b640997", "paper-2"],
            "paper_title": [
                "Noether Symmetries and Covariant Conservation Laws in Classical, Relativistic and Quantum Physics",
                "Paper Two Title",
            ],
        }
    )
    payload = {
        "items": [item],
        "page": 1,
        "pageSize": 10,
        "totalItems": 1,
        "totalPages": 1,
    }
    app = _build_route_app(admin=admin)

    with patch(
        "apps.learningspotlight.routes.list_learning_spotlight_users",
        new=AsyncMock(return_value=payload),
    ) as mocked:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/admin/learning-spotlight/users",
                params={"page": 1, "pageSize": 10, "status": "Active"},
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] is True
    assert body["message"] == "learning spotlight users listed"
    assert body["data"]["page"] == 1
    assert body["data"]["pageSize"] == 10
    assert body["data"]["totalItems"] == 1
    returned = body["data"]["items"][0]
    for field in EXISTING_USER_FIELDS:
        assert field in returned
    for field in SPOTLIGHT_FIELDS:
        assert field in returned
    assert returned["is_learning_spotlight_recommended"] is True
    assert returned["recommended_cycle_name"] == "Leading Thinker"
    assert returned["paper_id"] == [
        "003e3a6be8537162fd112b3a0a51a6063b640997",
        "paper-2",
    ]
    assert returned["paper_title"][0].startswith("Noether Symmetries")
    assert returned["user_id"] == str(user_id)
    mocked.assert_awaited_once()
    kwargs = mocked.await_args.kwargs
    assert mocked.await_args.args[0] == 1
    assert mocked.await_args.args[1] == 10
    assert kwargs["search"] is None
    assert kwargs["status"].value == "Active"
    assert kwargs["is_learning_spotlight_recommended"] is None


@pytest.mark.asyncio
async def test_admin_learning_spotlight_users_endpoint_forwards_search_and_filter() -> None:
    admin = User(id=uuid4(), email="admin@example.com", role="superadmin")
    app = _build_route_app(admin=admin)
    payload = {
        "items": [],
        "page": 1,
        "pageSize": 10,
        "totalItems": 0,
        "totalPages": 0,
    }

    with patch(
        "apps.learningspotlight.routes.list_learning_spotlight_users",
        new=AsyncMock(return_value=payload),
    ) as mocked:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/admin/learning-spotlight/users",
                params={
                    "search": "Prasad",
                    "is_learning_spotlight_recommended": "true",
                    "page": 1,
                    "pageSize": 10,
                },
            )

    assert resp.status_code == 200
    kwargs = mocked.await_args.kwargs
    assert kwargs["search"] == "Prasad"
    assert kwargs["is_learning_spotlight_recommended"] is True


@pytest.mark.asyncio
async def test_admin_learning_spotlight_users_endpoint_false_filter() -> None:
    admin = User(id=uuid4(), email="admin@example.com", role="superadmin")
    app = _build_route_app(admin=admin)
    payload = {
        "items": [],
        "page": 1,
        "pageSize": 10,
        "totalItems": 0,
        "totalPages": 0,
    }

    with patch(
        "apps.learningspotlight.routes.list_learning_spotlight_users",
        new=AsyncMock(return_value=payload),
    ) as mocked:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/admin/learning-spotlight/users",
                params={"is_learning_spotlight_recommended": "false"},
            )

    assert resp.status_code == 200
    assert mocked.await_args.kwargs["is_learning_spotlight_recommended"] is False


@pytest.mark.asyncio
async def test_admin_learning_spotlight_users_requires_admin() -> None:
    app = _build_route_app()

    async def _override_non_admin():
        raise HTTPException(status_code=403, detail="Forbidden: Admin access required")

    app.dependency_overrides[get_current_admin] = _override_non_admin
    app.dependency_overrides[require_signed_admin] = _override_non_admin
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/admin/learning-spotlight/users")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_learning_spotlight_users_unauthorized_without_token() -> None:
    app = _build_route_app()
    app.dependency_overrides.pop(get_current_admin, None)
    app.dependency_overrides.pop(require_signed_admin, None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/admin/learning-spotlight/users")
    assert resp.status_code == 401
    assert "Missing access token" in resp.json()["message"]


@pytest.mark.asyncio
async def test_admin_learning_spotlight_users_rejects_non_admin_role() -> None:
    app = _build_route_app()

    async def _override_app_user():
        raise ApiError("Insufficient permissions")

    app.dependency_overrides[get_current_admin] = _override_app_user
    app.dependency_overrides[require_signed_admin] = _override_app_user
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/admin/learning-spotlight/users")
    assert resp.status_code == 403
    assert resp.json()["status"] is False
    assert "Insufficient permissions" in resp.json()["message"]


def test_moderation_notes_entity_type_still_user() -> None:
    assert ReportEntityType.user.value == "user"
