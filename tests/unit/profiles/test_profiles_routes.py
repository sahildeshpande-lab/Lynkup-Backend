from fastapi.testclient import TestClient

from apps.accounts.db_models import User
from core.auth.dependencies import require_recent_auth
from core.database.session import get_session
from core.security.auth import get_current_user
from entrypoints.api import app


client = TestClient(app)


async def _override_current_user():
    return User(email="jane@example.com", role="user", firebase_uid="test-uid")


async def _override_recent_auth():
    pass


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
    app.dependency_overrides[require_recent_auth] = _override_recent_auth
    async def _override_session():
        yield _NoopSession()
    app.dependency_overrides[get_session] = _override_session


def teardown_module() -> None:
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(require_recent_auth, None)
    app.dependency_overrides.pop(get_session, None)


def test_update_visibility_returns_success(monkeypatch) -> None:
    from apps.profiles import services as profiles_services

    async def _mock_update_profile_visibility_service(user, payload, db):
        return {"profileVisibility": payload.profileVisibility}

    monkeypatch.setattr(profiles_services, "update_profile_visibility_service", _mock_update_profile_visibility_service)

    response = client.patch(
        "/api/v1/profilevisibility",
        json={"profileVisibility": "public"},
        headers={"Authorization": "Bearer access_test-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["data"]["profileVisibility"] == "public"


def test_patch_me_turns_off_onboarding(monkeypatch) -> None:
    from apps.profiles import services as profiles_services

    async def _mock_update_my_profile_service(user, payload, db):
        return {"user": {"bio": payload.bio, "is_onboarding_completed": True}}

    monkeypatch.setattr(profiles_services, "update_my_profile_service", _mock_update_my_profile_service)

    response = client.patch(
        "/api/v1/updateprofile",
        json={"bio": "updated bio"},
        headers={"Authorization": "Bearer access_test-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["data"]["user"]["is_onboarding_completed"] is True


def test_get_public_profile_returns_success() -> None:
    response = client.get("/api/v1/users/abc123@example.com")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["data"]["email"] == "abc123@example.com"


def test_get_me_requires_bearer_token() -> None:
    override = app.dependency_overrides.pop(get_current_user, None)
    try:
        response = client.get("/api/v1/myprofile")
        assert response.status_code == 200
        assert response.json()["status"] is False
    finally:
        if override:
            app.dependency_overrides[get_current_user] = override


def test_get_me_returns_user_payload(monkeypatch) -> None:
    from apps.profiles import services as profiles_services

    async def _mock_get_my_profile_service(user, db):
        return {"user": {
            "email": "jane@example.com",
            "is_onboarding_completed": False
        }}

    monkeypatch.setattr(profiles_services, "get_my_profile_service", _mock_get_my_profile_service)

    response = client.get("/api/v1/myprofile", headers={"Authorization": "Bearer access_jane@example.com"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["data"]["user"]["email"] == "jane@example.com"
    assert body["data"]["user"]["is_onboarding_completed"] is False


def test_get_me_completeness_returns_score(monkeypatch) -> None:
    from apps.profiles import services as profiles_services

    async def _mock_get_me_completeness(user_id, db):
        return {"completeness_score": 33}

    monkeypatch.setattr(profiles_services, "get_me_completeness", _mock_get_me_completeness)

    response = client.get(
        "/api/v1/users/me/completeness",
        headers={"Authorization": "Bearer access_jane@example.com"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["data"]["completeness_score"] == 33


def test_delete_user_me_returns_success(monkeypatch) -> None:
    from datetime import datetime, timezone

    from apps.profiles import services as profiles_services

    async def _mock_delete_user_me(user, db):
        return {"deleted": True, "status": "deleting", "deleted_at": datetime.now(timezone.utc).isoformat()}

    monkeypatch.setattr(profiles_services, "delete_user_me", _mock_delete_user_me)

    response = client.delete(
        "/api/v1/users/me/deletion",
        headers={"Authorization": "Bearer access_jane@example.com"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "user deletion scheduled"
    assert body["data"]["deleted"] is True
    assert body["data"]["status"] == "deleting"
    assert "deleted_at" in body["data"]


def test_complete_onboarding_returns_success_payload(monkeypatch) -> None:
    from apps.profiles import services as profiles_services

    async def _mock_complete_onboarding(
        user,
        bio,
        major,
        minor,
        university_id,
        education_level_id,
        academic_interests,
        profile_photo_key,
        banner_photo_key,
        db,
    ):
        assert education_level_id == 2
        return {"user": {"email": user.email}, "onboarded": True}

    monkeypatch.setattr(profiles_services, "complete_onboarding", _mock_complete_onboarding)

    response = client.post(
        "/api/v1/users/onboarding",
        json={
            "profile_photo_key": "profiles/test.png",
            "banner_photo_key": None,
            "university_id": "11111111-1111-1111-1111-111111111111",
            "major": "Computer Science",
            "minor": "Math",
            "education_level_id": 2,
            "bio": "Test Bio",
            "academic_interests": ["Math", "CS"]
        },
        headers={"Authorization": "Bearer access_jane@example.com"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "onboarding completed"
    assert body["data"]["onboarded"] is True
