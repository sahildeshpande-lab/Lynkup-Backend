from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.academics import routes as academics_routes
from common.exceptions import ApiError
from apps.accounts.db_models import User
from core.database.session import get_session
from core.security.auth import get_current_admin, get_current_user_moderator_or_superadmin
from entrypoints.api import DISCOVERY_TAG, app
from apps.administration.dependencies import require_signed_admin


client = TestClient(app)


class _NoopSession:
    async def execute(self, *_args, **_kwargs):
        raise AssertionError("db session should not be used in this route test")

    def add(self, *_args, **_kwargs):
        return None

    async def commit(self):
        return None

    async def refresh(self, *_args, **_kwargs):
        return None

    async def flush(self):
        return None

    async def rollback(self):
        return None


async def _override_session():
    yield _NoopSession()


async def _override_admin():
    user = User(
        id="11111111-1111-1111-1111-111111111111",
        email="admin@example.com",
        firebase_uid="admin-uid",
    )
    user.role = "superadmin"
    return user


async def _override_app_user():
    user = User(
        id="55555555-5555-5555-5555-555555555555",
        email="appuser@example.com",
        firebase_uid="app-user-uid",
    )
    user.role = "user"
    return user


def setup_module() -> None:
    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_admin] = _override_admin
    app.dependency_overrides[require_signed_admin] = _override_admin
    app.dependency_overrides[get_current_user_moderator_or_superadmin] = _override_app_user


def teardown_module() -> None:
    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(get_current_admin, None)
    app.dependency_overrides.pop(require_signed_admin, None)
    app.dependency_overrides.pop(get_current_user_moderator_or_superadmin, None)


def test_admin_major_route(monkeypatch) -> None:
    async def _bulk_create_majors(items, db) -> dict:
        assert len(items) == 2
        assert items[0].name == "Accounting"
        return {
            "total": 2,
            "created": 2,
            "existing": 0,
            "failed": 0,
            "items": [{"id": "1", "name": "Accounting"}, {"id": "2", "name": "Computer Science"}],
            "already_existing": [],
            "failures": [],
        }

    monkeypatch.setattr(academics_routes.services, "bulk_create_majors", _bulk_create_majors)
    response = client.post(
        "/api/v1/admin/major",
        json={"items": [{"name": "Accounting"}, {"name": "Computer Science"}]},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "2 majors added successfully"
    assert body["data"]["created"] == 2
    assert body["data"]["already_existing"] == []
    assert body["data"]["failures"] == []
    assert len(body["data"]["items"]) == 2


def test_admin_minor_route(monkeypatch) -> None:
    async def _bulk_create_minors(items, db) -> dict:
        assert len(items) == 1
        assert items[0].name == "Artificial Intelligence"
        return {
            "total": 1,
            "created": 1,
            "existing": 0,
            "failed": 0,
            "items": [{"id": "1", "name": "Artificial Intelligence"}],
            "already_existing": [],
            "failures": [],
        }

    monkeypatch.setattr(academics_routes.services, "bulk_create_minors", _bulk_create_minors)
    response = client.post(
        "/api/v1/admin/minor",
        json={"items": [{"name": "Artificial Intelligence"}]},
    )
    assert response.status_code == 201
    assert response.json()["data"]["created"] == 1
    assert response.json()["data"]["already_existing"] == []


def _item_get(item, *keys, default=None):
    for key in keys:
        if isinstance(item, dict):
            if key in item:
                return item[key]
            continue
        if hasattr(item, key):
            return getattr(item, key)
    return default


def test_admin_academic_interest_soft_fails_blank_major(monkeypatch) -> None:
    async def _bulk_create_academic_interests(items, db) -> dict:
        assert _item_get(items[0], "name") == "Deep Learning"
        assert _item_get(items[0], "major", "major_id") is None
        return {
            "total": 1,
            "created": 0,
            "existing": 0,
            "failed": 1,
            "items": [],
            "already_existing": [],
            "failures": [{"name": "Deep Learning", "reason": "major cannot be blank"}],
        }

    monkeypatch.setattr(
        academics_routes.services,
        "bulk_create_academic_interests",
        _bulk_create_academic_interests,
    )
    response = client.post(
        "/api/v1/admin/academic-interest",
        json={
            "items": [
                {"name": "Deep Learning", "major_id": None, "minor_id": 10},
            ]
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] is False
    assert body["message"] == "1 interests failed to create"
    assert body["data"]["failed"] == 1
    assert body["data"]["failures"][0]["reason"] == "major cannot be blank"


def test_admin_academic_interest_route(monkeypatch) -> None:
    async def _bulk_create_academic_interests(items, db) -> dict:
        assert _item_get(items[0], "major", "major_id") == 1
        assert _item_get(items[0], "minor", "minor_id") is None
        assert _item_get(items[1], "minor", "minor_id") == 10
        return {
            "total": 2,
            "created": 2,
            "existing": 0,
            "failed": 0,
            "items": [{"id": "1"}, {"id": "2"}],
            "already_existing": [],
            "failures": [],
        }

    monkeypatch.setattr(
        academics_routes.services,
        "bulk_create_academic_interests",
        _bulk_create_academic_interests,
    )
    response = client.post(
        "/api/v1/admin/academic-interest",
        json={
            "items": [
                {"name": "Algorithms", "major_id": 1, "minor_id": None},
                {"name": "Machine Learning", "major_id": 1, "minor_id": 10},
            ]
        },
    )
    assert response.status_code == 201
    assert response.json()["data"]["created"] == 2


def test_admin_academic_interest_accepts_major_minor_names(monkeypatch) -> None:
    async def _bulk_create_academic_interests(items, db) -> dict:
        assert _item_get(items[0], "major", "major_id") == "batista"
        assert _item_get(items[0], "minor", "minor_id") == "undertaker"
        assert _item_get(items[1], "major", "major_id") == "batista"
        return {
            "total": 2,
            "created": 2,
            "existing": 0,
            "failed": 0,
            "items": [],
            "already_existing": [],
            "failures": [],
        }

    monkeypatch.setattr(
        academics_routes.services,
        "bulk_create_academic_interests",
        _bulk_create_academic_interests,
    )
    response = client.post(
        "/api/v1/admin/academic-interest",
        json={
            "items": [
                {"name": "Aaaaaaaa", "major": "batista", "minor": "undertaker"},
                {"name": "Bbbbbb", "major": "batista", "minor": "undertaker"},
            ]
        },
    )
    assert response.status_code == 201
    assert response.json()["status"] is True


def test_admin_major_already_present(monkeypatch) -> None:
    async def _bulk_create_majors(items, db) -> dict:
        return {
            "total": 1,
            "created": 0,
            "existing": 1,
            "failed": 0,
            "items": [],
            "already_existing": [{"id": "1", "name": "Accounting", "isActive": True}],
            "failures": [],
        }

    monkeypatch.setattr(academics_routes.services, "bulk_create_majors", _bulk_create_majors)
    response = client.post(
        "/api/v1/admin/major",
        json={"items": [{"name": "Accounting"}]},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] is False
    assert body["message"] == "Accounting already exists"
    assert body["data"]["items"] == []
    assert body["data"]["already_existing"][0]["name"] == "Accounting"
    assert body["data"]["failures"] == []


def test_admin_major_already_present_import_keeps_status_true(monkeypatch) -> None:
    async def _bulk_create_majors(items, db) -> dict:
        return {
            "total": 1,
            "created": 0,
            "existing": 1,
            "failed": 0,
            "items": [],
            "already_existing": [{"id": "1", "name": "Accounting", "isActive": True}],
            "failures": [],
        }

    monkeypatch.setattr(academics_routes.services, "bulk_create_majors", _bulk_create_majors)
    response = client.post(
        "/api/v1/admin/major",
        params={"type": "import"},
        json={"items": [{"name": "Accounting"}]},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "1 majors already exists"
    assert body["data"]["already_existing"][0]["name"] == "Accounting"


def test_admin_major_soft_fails_invalid_names_keeps_valid(monkeypatch) -> None:
    async def _bulk_create_majors(items, db) -> dict:
        assert len(items) == 3
        # Invalid rows arrive as dicts; valid rows may be models or dicts.
        names = [
            item.name if hasattr(item, "name") else item.get("name") for item in items
        ]
        assert "Computer Science" in names
        assert "1234" in names
        assert "Web3" in names
        return {
            "total": 3,
            "created": 2,
            "existing": 0,
            "failed": 1,
            "items": [
                {"id": "1", "name": "Computer Science"},
                {"id": "2", "name": "Web3"},
            ],
            "already_existing": [],
            "failures": [
                {
                    "name": "1234",
                    "reason": (
                        "name must contain at least one letter "
                        "(cannot be only numbers or special characters)"
                    ),
                }
            ],
        }

    monkeypatch.setattr(academics_routes.services, "bulk_create_majors", _bulk_create_majors)
    response = client.post(
        "/api/v1/admin/major",
        json={
            "items": [
                {"name": "Computer Science"},
                {"name": "1234"},
                {"name": "Web3"},
            ]
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] is False
    assert body["data"] is not None
    assert body["data"]["created"] == 2
    assert body["data"]["failed"] == 1
    assert body["data"]["failures"][0]["name"] == "1234"
    assert "Invalid name" not in body["message"]

    import_response = client.post(
        "/api/v1/admin/major",
        params={"type": "import"},
        json={
            "items": [
                {"name": "Computer Science"},
                {"name": "1234"},
                {"name": "Web3"},
            ]
        },
    )
    assert import_response.status_code == 201
    assert import_response.json()["status"] is True


def test_admin_country_soft_fails_invalid_name_keeps_valid(monkeypatch) -> None:
    async def _bulk_create_countries(items, db) -> dict:
        assert len(items) == 2
        return {
            "total": 2,
            "created": 1,
            "existing": 0,
            "failed": 1,
            "items": [{"id": "1", "name": "India", "isoCode": "IN"}],
            "already_existing": [],
            "failures": [
                {
                    "name": "USA1",
                    "isoCode": "U1",
                    "reason": (
                        "name may only contain letters, spaces, and hyphens (-) "
                        "(numbers and special characters are not allowed)"
                    ),
                }
            ],
        }

    monkeypatch.setattr(
        academics_routes.services, "bulk_create_countries", _bulk_create_countries
    )
    response = client.post(
        "/api/v1/admin/country",
        json={
            "items": [
                {"name": "USA1", "iso_code": "U1"},
                {"name": "India", "iso_code": "IN"},
            ]
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] is False
    assert body["data"]["created"] == 1
    assert body["data"]["failed"] == 1
    assert "Invalid name" not in body["message"]


def test_admin_university_soft_fails_invalid_name_keeps_valid(monkeypatch) -> None:
    async def _bulk_create_universities(items, db) -> dict:
        assert len(items) == 2
        return {
            "total": 2,
            "created": 1,
            "existing": 0,
            "failed": 1,
            "items": [{"id": "1", "name": "Texas A&M"}],
            "already_existing": [],
            "failures": [
                {
                    "name": "1234",
                    "reason": (
                        "name must contain at least 2 letters "
                        "(cannot be only numbers or special characters)"
                    ),
                }
            ],
        }

    monkeypatch.setattr(
        academics_routes.services, "bulk_create_universities", _bulk_create_universities
    )
    response = client.post(
        "/api/v1/admin/university",
        json={
            "items": [
                {
                    "name": "1234",
                    "slug": "bad",
                    "country": "India",
                    "website": "https://bad.edu",
                },
                {
                    "name": "Texas A&M",
                    "slug": "texas-am",
                    "country": "India",
                    "website": "https://tamu.edu",
                },
            ]
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] is False
    assert body["data"]["created"] == 1
    assert body["data"]["failed"] == 1
    assert "Invalid name" not in body["message"]


def test_admin_academic_interest_soft_fails_invalid_name_keeps_valid(monkeypatch) -> None:
    async def _bulk_create_academic_interests(items, db) -> dict:
        assert len(items) == 2
        return {
            "total": 2,
            "created": 1,
            "existing": 0,
            "failed": 1,
            "items": [{"id": "1", "name": "Deep Learning"}],
            "already_existing": [],
            "failures": [
                {
                    "name": "!!!",
                    "reason": (
                        "name must contain at least one letter "
                        "(cannot be only numbers or special characters)"
                    ),
                }
            ],
        }

    monkeypatch.setattr(
        academics_routes.services,
        "bulk_create_academic_interests",
        _bulk_create_academic_interests,
    )
    response = client.post(
        "/api/v1/admin/academic-interest",
        json={
            "items": [
                {"name": "!!!", "major": 1},
                {"name": "Deep Learning", "major": 1},
            ]
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] is False
    assert body["data"]["created"] == 1
    assert body["data"]["failed"] == 1
    assert "Invalid name" not in body["message"]


def test_admin_minor_already_present(monkeypatch) -> None:
    async def _bulk_create_minors(items, db) -> dict:
        return {
            "total": 1,
            "created": 0,
            "existing": 1,
            "failed": 0,
            "items": [],
            "already_existing": [
                {"id": "1", "name": "Artificial Intelligence", "isActive": True}
            ],
            "failures": [],
        }

    monkeypatch.setattr(academics_routes.services, "bulk_create_minors", _bulk_create_minors)
    response = client.post(
        "/api/v1/admin/minor",
        json={"items": [{"name": "Artificial Intelligence"}]},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] is False
    assert body["message"] == "Artificial Intelligence already exists"
    assert body["data"]["items"] == []
    assert len(body["data"]["already_existing"]) == 1


def test_admin_academic_interest_already_present(monkeypatch) -> None:
    async def _bulk_create_academic_interests(items, db) -> dict:
        return {
            "total": 1,
            "created": 0,
            "existing": 1,
            "failed": 0,
            "items": [],
            "already_existing": [
                {"id": "1", "name": "DL", "majorId": "1", "minorId": None, "isActive": True}
            ],
            "failures": [],
        }

    monkeypatch.setattr(
        academics_routes.services,
        "bulk_create_academic_interests",
        _bulk_create_academic_interests,
    )
    response = client.post(
        "/api/v1/admin/academic-interest",
        json={"items": [{"name": "DL", "major_id": 1}]},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] is False
    assert body["message"] == "DL already exists"
    assert body["data"]["items"] == []
    assert body["data"]["already_existing"][0]["name"] == "DL"


def test_admin_country_already_present_add_cta_uses_name_message(monkeypatch) -> None:
    async def _bulk_create_countries(items, db) -> dict:
        return {
            "total": 1,
            "created": 0,
            "existing": 1,
            "failed": 0,
            "items": [],
            "already_existing": [
                {
                    "id": "905b09d7-a559-5bfe-9c23-0c470ab10fa9",
                    "name": "Test Country",
                    "isoCode": "TC",
                    "isActive": True,
                }
            ],
            "failures": [],
        }

    monkeypatch.setattr(academics_routes.services, "bulk_create_countries", _bulk_create_countries)
    response = client.post(
        "/api/v1/admin/country",
        json={"items": [{"name": "Test Country", "iso_code": "TC", "is_active": True}]},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] is False
    assert body["message"] == "Test Country already exists"
    assert body["data"]["already_existing"][0]["name"] == "Test Country"


def test_admin_university_already_present_add_cta_uses_name_message(monkeypatch) -> None:
    async def _bulk_create_universities(items, db) -> dict:
        return {
            "total": 1,
            "created": 0,
            "existing": 1,
            "failed": 0,
            "items": [],
            "already_existing": [
                {"id": "1", "name": "Example University", "slug": "example-university"}
            ],
            "failures": [],
        }

    monkeypatch.setattr(
        academics_routes.services, "bulk_create_universities", _bulk_create_universities
    )
    response = client.post(
        "/api/v1/admin/university",
        json={
            "items": [
                {
                    "name": "Example University",
                    "slug": "example-university",
                    "country": "India",
                    "website": "https://example.edu",
                }
            ]
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] is False
    assert body["message"] == "Example University already exists"


def test_admin_university_and_country_routes(monkeypatch) -> None:
    async def _bulk_create_universities(items, db) -> dict:
        assert _item_get(items[0], "name") == "Example University"
        assert _item_get(items[0], "is_active", default=True) is True
        assert str(_item_get(items[0], "country", "country_id")) == (
            "11111111-1111-1111-1111-111111111111"
        )
        return {
            "total": 1,
            "created": 1,
            "existing": 0,
            "failed": 0,
            "items": [{"id": "1", "name": "Example University"}],
            "already_existing": [],
            "failures": [],
        }

    async def _bulk_create_countries(items, db) -> dict:
        assert _item_get(items[0], "iso_code") == "IN"
        assert _item_get(items[1], "is_active") is False
        return {
            "total": 2,
            "created": 2,
            "existing": 0,
            "failed": 0,
            "items": [{"isoCode": "IN"}, {"isoCode": "US"}],
            "already_existing": [],
            "failures": [],
        }

    monkeypatch.setattr(
        academics_routes.services, "bulk_create_universities", _bulk_create_universities
    )
    monkeypatch.setattr(academics_routes.services, "bulk_create_countries", _bulk_create_countries)

    country_response = client.post(
        "/api/v1/admin/country",
        json={
            "items": [
                {"name": "India", "iso_code": "IN"},
                {"name": "United States", "iso_code": "US", "is_active": False},
            ]
        },
    )
    assert country_response.status_code == 201
    assert country_response.json()["message"] == "2 countries added successfully"
    assert country_response.json()["data"]["already_existing"] == []

    university_response = client.post(
        "/api/v1/admin/university",
        json={
            "items": [
                {
                    "name": "Example University",
                    "slug": "example-university",
                    "country_id": "11111111-1111-1111-1111-111111111111",
                    "major": ["Computer Science"],
                    "minor": ["Artificial Intelligence"],
                    "academic_program": ["B.Tech"],
                    "website": "https://example.edu",
                }
            ]
        },
    )
    assert university_response.status_code == 201
    assert university_response.json()["status"] is True
    assert university_response.json()["data"]["failures"] == []


def test_admin_country_reports_invalid_iso_format_as_failed(monkeypatch) -> None:
    async def _bulk_create_countries(items, db) -> dict:
        assert _item_get(items[0], "iso_code") == "USA"
        assert _item_get(items[1], "iso_code") == "IN"
        return {
            "total": 2,
            "created": 1,
            "existing": 0,
            "failed": 1,
            "items": [{"id": "1", "name": "India", "isoCode": "IN"}],
            "already_existing": [],
            "failures": [
                {
                    "name": "United States of America",
                    "isoCode": "USA",
                    "reason": "ISO code format does not match",
                }
            ],
        }

    monkeypatch.setattr(academics_routes.services, "bulk_create_countries", _bulk_create_countries)
    response = client.post(
        "/api/v1/admin/country",
        json={
            "items": [
                {"name": "United States of America", "iso_code": "USA"},
                {"name": "India", "iso_code": "IN"},
            ]
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] is False
    assert body["message"] == (
        "1 countries added successfully, 1 countries failed to create"
    )
    assert body["data"]["created"] == 1
    assert body["data"]["failed"] == 1
    assert body["data"]["items"][0]["isoCode"] == "IN"
    assert body["data"]["already_existing"] == []
    assert body["data"]["failures"][0]["reason"] == "ISO code format does not match"

    import_response = client.post(
        "/api/v1/admin/country",
        params={"type": "import"},
        json={
            "items": [
                {"name": "United States of America", "iso_code": "USA"},
                {"name": "India", "iso_code": "IN"},
            ]
        },
    )
    assert import_response.status_code == 201
    assert import_response.json()["status"] is True


def test_admin_university_reports_missing_country_as_failed(monkeypatch) -> None:
    async def _bulk_create_universities(items, db) -> dict:
        _ = items, db
        return {
            "total": 1,
            "created": 0,
            "existing": 0,
            "failed": 1,
            "items": [],
            "already_existing": [],
            "failures": [{"name": "Missing Country University", "reason": "country not found"}],
        }

    monkeypatch.setattr(
        academics_routes.services, "bulk_create_universities", _bulk_create_universities
    )
    response = client.post(
        "/api/v1/admin/university",
        json={
            "items": [
                {
                    "name": "Missing Country University",
                    "slug": "missing-country-university",
                    "country": "11111111-1111-1111-1111-111111111111",
                    "website": "https://missing-country.edu",
                }
            ]
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] is False
    assert body["message"] == "1 universities failed to create"
    assert body["data"]["failed"] == 1
    assert body["data"]["items"] == []
    assert body["data"]["already_existing"] == []
    assert body["data"]["failures"][0]["reason"] == "country not found"


def test_admin_academic_interest_reports_missing_major_as_failed(monkeypatch) -> None:
    async def _bulk_create_academic_interests(items, db) -> dict:
        _ = items, db
        return {
            "total": 1,
            "created": 0,
            "existing": 0,
            "failed": 1,
            "items": [],
            "already_existing": [],
            "failures": [{"name": "Algorithms", "reason": "major not found"}],
        }

    monkeypatch.setattr(
        academics_routes.services,
        "bulk_create_academic_interests",
        _bulk_create_academic_interests,
    )
    response = client.post(
        "/api/v1/admin/academic-interest",
        json={"items": [{"name": "Algorithms", "major": 999999}]},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] is False
    assert body["message"] == "1 interests failed to create"
    assert body["data"]["failures"][0]["reason"] == "major not found"


def test_admin_academic_interest_mixed_outcome_message(monkeypatch) -> None:
    async def _bulk_create_academic_interests(items, db) -> dict:
        _ = items, db
        return {
            "total": 9,
            "created": 3,
            "existing": 4,
            "failed": 2,
            "items": [{"id": "1"}, {"id": "2"}, {"id": "3"}],
            "already_existing": [
                {"id": "21362", "name": "Testing major interest"},
                {"id": "21361", "name": "Test interest"},
                {"id": "21363", "name": "Test Academic 2"},
                {"id": "21365", "name": "Major testing interest"},
            ],
            "failures": [
                {"name": "Test Academic 2 new new", "reason": "major not found"},
                {"name": "Test Academic 2 new", "reason": "minor not found"},
            ],
        }

    monkeypatch.setattr(
        academics_routes.services,
        "bulk_create_academic_interests",
        _bulk_create_academic_interests,
    )
    response = client.post(
        "/api/v1/admin/academic-interest",
        json={"items": [{"name": "Algorithms", "major": 1}]},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] is False
    assert body["message"] == (
        "3 interests added successfully, 2 interests failed to create, "
        "4 interests already exists"
    )
    assert body["data"]["created"] == 3
    assert body["data"]["existing"] == 4
    assert body["data"]["failed"] == 2
    assert len(body["data"]["already_existing"]) == 4
    assert len(body["data"]["failures"]) == 2

    import_response = client.post(
        "/api/v1/admin/academic-interest",
        params={"type": "import"},
        json={"items": [{"name": "Algorithms", "major": 1}]},
    )
    assert import_response.status_code == 201
    assert import_response.json()["status"] is True


def test_admin_university_accepts_country_name(monkeypatch) -> None:
    async def _bulk_create_universities(items, db) -> dict:
        assert _item_get(items[0], "country", "country_id") == "India"
        return {
            "total": 1,
            "created": 1,
            "existing": 0,
            "failed": 0,
            "items": [{"id": "1"}],
            "already_existing": [],
            "failures": [],
        }

    monkeypatch.setattr(
        academics_routes.services, "bulk_create_universities", _bulk_create_universities
    )
    response = client.post(
        "/api/v1/admin/university",
        json={
            "items": [
                {
                    "name": "Named Country University",
                    "slug": "named-country-university",
                    "country": "India",
                    "major": ["Computer Science"],
                    "minor": ["Artificial Intelligence"],
                    "website": "https://named.edu",
                }
            ]
        },
    )
    assert response.status_code == 201
    assert response.json()["status"] is True


def test_admin_bulk_response_openapi_schema() -> None:
    schemas = app.openapi()["components"]["schemas"]
    assert "CatalogBulkProcessResult" in schemas
    props = schemas["CatalogBulkProcessResult"]["properties"]
    for key in (
        "total",
        "created",
        "existing",
        "failed",
        "items",
        "already_existing",
        "failures",
    ):
        assert key in props, key

    paths = app.openapi()["paths"]
    for path in (
        "/api/v1/admin/major",
        "/api/v1/admin/minor",
        "/api/v1/admin/academic-interest",
        "/api/v1/admin/university",
        "/api/v1/admin/country",
        "/api/v1/admin/academics",
    ):
        method = "patch" if path.endswith("/academics") else "post"
        response_schema = paths[path][method]["responses"]["200" if method == "patch" else "201"][
            "content"
        ]["application/json"]["schema"]
        assert "$ref" in response_schema
        assert response_schema["$ref"].endswith("CatalogBulkApiResponse")
        if method == "post":
            params = paths[path][method].get("parameters") or []
            type_param = next((p for p in params if p.get("name") == "type"), None)
            assert type_param is not None, path
            assert type_param.get("in") == "query"
            assert type_param.get("required") is False


def test_admin_catalog_rejects_invalid_type_query() -> None:
    response = client.post(
        "/api/v1/admin/major",
        params={"type": "create"},
        json={"items": [{"name": "Accounting"}]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] is False
    assert "type" in body["message"].lower()


def test_get_test_catalog_routes(monkeypatch) -> None:
    async def _list_test_majors(query, page, page_size, db, *args, **kwargs) -> dict:
        assert page is None
        assert page_size is None
        return {
            "items": [{"id": "1", "name": "Computer Science", "major_added_by": "admin"}],
            "page": 1,
            "pageSize": 1,
            "totalItems": 1,
            "totalPages": 1,
        }

    async def _list_test_minors(query, page, page_size, db, *args, **kwargs) -> dict:
        assert page is None
        assert page_size is None
        return {
            "items": [{"id": "2", "name": "Artificial Intelligence", "minor_added_by": "admin"}],
            "page": 1,
            "pageSize": 1,
            "totalItems": 1,
            "totalPages": 1,
        }

    async def _list_test_interests(*, major_id, minor_id, query, page, page_size, db, **kwargs) -> dict:
        assert major_id == 1
        assert minor_id == 10
        assert page is None
        assert page_size is None
        return {
            "items": [
                {
                    "id": "3",
                    "name": "Algorithms",
                    "majorName": "Computer Science",
                    "minorName": None,
                    "interest_added_by": "admin",
                },
                {
                    "id": "4",
                    "name": "Deep Learning",
                    "majorName": "Computer Science",
                    "minorName": "Artificial Intelligence",
                    "interest_added_by": "user",
                },
            ],
            "page": 1,
            "pageSize": 2,
            "totalItems": 2,
            "totalPages": 1,
        }

    monkeypatch.setattr(academics_routes.services, "list_test_majors", _list_test_majors)
    monkeypatch.setattr(academics_routes.services, "list_test_minors", _list_test_minors)
    monkeypatch.setattr(academics_routes.services, "list_test_interests", _list_test_interests)

    majors = client.get("/api/v1/major")
    assert majors.status_code == 200
    assert majors.json()["data"]["items"][0]["name"] == "Computer Science"
    assert majors.json()["data"]["items"][0]["major_added_by"] == "admin"

    minors = client.get("/api/v1/minors")
    assert minors.status_code == 200
    assert minors.json()["data"]["items"][0]["name"] == "Artificial Intelligence"
    assert minors.json()["data"]["items"][0]["minor_added_by"] == "admin"

    interests = client.get("/api/v1/interest", params={"major_id": 1, "minor_id": 10})
    assert interests.status_code == 200
    assert len(interests.json()["data"]["items"]) == 2
    assert interests.json()["data"]["items"][0]["interest_added_by"] == "admin"
    assert interests.json()["data"]["items"][0]["majorName"] == "Computer Science"


def test_get_test_catalog_routes_pass_page_and_page_size(monkeypatch) -> None:
    async def _list_test_majors(query, page, page_size, db, *args, **kwargs) -> dict:
        assert page == 2
        assert page_size == 10
        return {
            "items": [{"id": "11", "name": "Biology"}],
            "page": 2,
            "pageSize": 10,
            "totalItems": 25,
            "totalPages": 3,
        }

    monkeypatch.setattr(academics_routes.services, "list_test_majors", _list_test_majors)
    response = client.get("/api/v1/major", params={"page": 2, "pageSize": 10})
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["page"] == 2
    assert body["pageSize"] == 10
    assert body["totalItems"] == 25
    assert body["totalPages"] == 3


def test_get_test_catalog_routes_ignore_partial_pagination(monkeypatch) -> None:
    async def _list_test_majors(query, page, page_size, db, *args, **kwargs) -> dict:
        assert page == 2
        assert page_size is None
        return {
            "items": [{"id": "1", "name": "Computer Science"}],
            "page": 1,
            "pageSize": 1,
            "totalItems": 1,
            "totalPages": 1,
        }

    monkeypatch.setattr(academics_routes.services, "list_test_majors", _list_test_majors)
    response = client.get("/api/v1/major", params={"page": 2})
    assert response.status_code == 200
    assert response.json()["data"]["totalItems"] == 1


def test_get_test_interests_without_major_id_returns_all(monkeypatch) -> None:
    async def _list_test_interests(*, major_id, minor_id, query, page, page_size, db, **kwargs) -> dict:
        assert major_id is None
        assert minor_id is None
        return {
            "items": [
                {
                    "id": "3",
                    "name": "Algorithms",
                    "majorName": "Computer Science",
                    "minorName": None,
                    "interest_added_by": "admin",
                }
            ],
            "page": 1,
            "pageSize": 1,
            "totalItems": 1,
            "totalPages": 1,
        }

    monkeypatch.setattr(academics_routes.services, "list_test_interests", _list_test_interests)
    response = client.get("/api/v1/interest")
    assert response.status_code == 200
    body = response.json()

    assert body["status"] is True
    assert body["data"]["items"][0]["majorName"] == "Computer Science"
    assert body["data"]["items"][0]["minorName"] is None


def test_catalog_lookup_routes_are_in_search_and_discovery() -> None:
    paths = app.openapi()["paths"]
    for path in ("/api/v1/major", "/api/v1/minors", "/api/v1/interest"):
        assert DISCOVERY_TAG in paths[path]["get"]["tags"]
        assert path in paths
    assert "/api/v1/test/major" not in paths
    assert "/api/v1/test/minor" not in paths
    assert "/api/v1/test/interest" not in paths
    assert "/api/v1/admin/major" in paths
    assert "4] Admin Management" in paths["/api/v1/admin/major"]["post"]["tags"]


def _admin_item_schema(paths: dict, path: str) -> dict:
    schema = paths[path]["post"]["requestBody"]["content"]["application/json"]["schema"]
    components = app.openapi()["components"]["schemas"]

    def _resolve(node: dict) -> dict:
        while "$ref" in node:
            node = components[node["$ref"].split("/")[-1]]
        if "anyOf" in node:
            for option in node["anyOf"]:
                candidate = _resolve(option)
                if candidate.get("properties"):
                    return candidate
            return _resolve(node["anyOf"][0])
        return node

    schema = _resolve(schema)
    items = schema["properties"]["items"]
    item_schema = items["items"] if "items" in items else items
    return _resolve(item_schema)


@pytest.mark.parametrize(
    "catalog_type",
    ["major", "minor", "academic_interest", "university", "country"],
)
def test_admin_soft_delete_catalog_route(monkeypatch, catalog_type) -> None:
    async def _soft_delete(received_type, received_id, db) -> dict:
        assert received_type.value == catalog_type
        assert received_id == "42"
        return {
            "type": catalog_type,
            "id": "42",
            "isActive": False,
            "updatedAt": "2026-08-20T00:00:00+00:00",
        }

    monkeypatch.setattr(
        academics_routes.services, "soft_delete_catalog_record", _soft_delete
    )
    response = client.request(
        "DELETE",
        "/api/v1/admin/academics",
        json={"type": catalog_type, "id": "42"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Academic catalog record deactivated successfully"
    assert body["data"]["type"] == catalog_type
    assert body["data"]["isActive"] is False


def test_admin_soft_delete_rejects_invalid_type() -> None:
    response = client.request(
        "DELETE",
        "/api/v1/admin/academics",
        json={"type": "degree", "id": "1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] is False
    assert "type" in body["message"].lower()


def test_admin_soft_delete_invalid_id_returns_not_found(monkeypatch) -> None:
    async def _soft_delete(received_type, received_id, db) -> dict:
        raise ApiError("Major not found")

    monkeypatch.setattr(
        academics_routes.services, "soft_delete_catalog_record", _soft_delete
    )
    response = client.request(
        "DELETE",
        "/api/v1/admin/academics",
        json={"type": "major", "id": "999999"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] is False
    assert body["message"] == "Major not found"


def test_admin_soft_delete_already_inactive_is_success(monkeypatch) -> None:
    async def _soft_delete(received_type, received_id, db) -> dict:
        return {
            "type": "major",
            "id": received_id,
            "isActive": False,
            "updatedAt": "2026-08-20T00:00:00+00:00",
        }

    monkeypatch.setattr(
        academics_routes.services, "soft_delete_catalog_record", _soft_delete
    )
    response = client.request(
        "DELETE",
        "/api/v1/admin/academics",
        json={"type": "major", "id": "7"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["data"]["isActive"] is False


def test_admin_soft_delete_route_is_registered() -> None:
    paths = app.openapi()["paths"]
    assert "/api/v1/admin/academics" in paths
    assert "delete" in paths["/api/v1/admin/academics"]
    assert "4] Admin Management" in paths["/api/v1/admin/academics"]["delete"]["tags"]


def test_admin_catalog_request_items_do_not_include_id() -> None:
    paths = app.openapi()["paths"]
    admin_paths = (
        "/api/v1/admin/major",
        "/api/v1/admin/minor",
        "/api/v1/admin/academic-interest",
        "/api/v1/admin/university",
        "/api/v1/admin/country",
    )
    for path in admin_paths:
        properties = _admin_item_schema(paths, path)["properties"]
        assert "id" not in properties, path


def test_user_academic_interest_route_creates_from_names(monkeypatch) -> None:
    async def _create_user_academic_interests(*, major, minor, interests, db) -> dict:
        assert major == "Computer Science"
        assert minor == "Artificial Intelligence"
        assert interests == ["Machine Learning", "Deep Learning"]
        return {
            "major": {"id": 10, "name": major, "major_added_by": "user"},
            "minor": {"id": 20, "name": minor, "minor_added_by": "user"},
            "items": [
                {
                    "id": 101,
                    "name": "Machine Learning",
                    "major_id": 10,
                    "minor_id": 20,
                    "education_level_id": None,
                    "interest_added_by": "user",
                }
            ],
        }

    monkeypatch.setattr(
        academics_routes.services,
        "create_user_academic_interests",
        _create_user_academic_interests,
    )
    response = client.post(
        "/api/v1/academic-interest",
        json={
            "major": "Computer Science",
            "minor": "Artificial Intelligence",
            "interests": ["Machine Learning", "Deep Learning"],
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Academic interests processed successfully"
    assert body["data"]["major"]["major_added_by"] == "user"
    assert body["data"]["minor"]["minor_added_by"] == "user"
    assert body["data"]["items"][0]["interest_added_by"] == "user"


def test_user_academic_interest_route_accepts_ids(monkeypatch) -> None:
    async def _create_user_academic_interests(*, major, minor, interests, db) -> dict:
        assert major == 12
        assert minor == 25
        assert interests == ["Machine Learning"]
        return {
            "major": {"id": 12, "name": "Computer Science", "major_added_by": "admin"},
            "minor": {"id": 25, "name": "Artificial Intelligence", "minor_added_by": "admin"},
            "items": [
                {
                    "id": 101,
                    "name": "Machine Learning",
                    "major_id": 12,
                    "minor_id": 25,
                    "education_level_id": None,
                    "interest_added_by": "user",
                }
            ],
        }

    monkeypatch.setattr(
        academics_routes.services,
        "create_user_academic_interests",
        _create_user_academic_interests,
    )
    response = client.post(
        "/api/v1/academic-interest",
        json={"major": 12, "minor": 25, "interests": ["Machine Learning"]},
    )
    assert response.status_code == 201
    assert response.json()["status"] is True


def test_user_academic_interest_route_rejects_empty_interests() -> None:
    response = client.post(
        "/api/v1/academic-interest",
        json={"major": "Computer Science", "interests": []},
    )
    assert response.status_code == 200
    assert response.json()["status"] is False


def test_legacy_academic_interests_endpoint_is_unchanged() -> None:
    paths = app.openapi()["paths"]
    assert "/api/v1/academic-interests" in paths
    assert "/api/v1/academic-interest" in paths
    assert "post" in paths["/api/v1/academic-interests"]
    assert DISCOVERY_TAG in paths["/api/v1/academic-interest"]["post"]["tags"]

