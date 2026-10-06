from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from apps.accounts.schemas import EmailSignupRequest, LoginRequest, SocialAuthRequest
from apps.accounts.services import auth_service as auth_svc
from apps.accounts.services import registration_service as reg_svc
from common.enums import RegistrationType, UserStatus
from common.exceptions import ApiError


def _mock_db(**kwargs) -> Mock:
    db = Mock(**kwargs)
    db.rollback = AsyncMock()
    return db


@pytest.mark.parametrize("role", ["moderator", "viewer", "superadmin"])
def test_signup_schema_rejects_staff_roles(role: str) -> None:
    with pytest.raises(ValidationError):
        EmailSignupRequest(
            firstName="Jane",
            lastName="Doe",
            email="jane@example.com",
            password="ValidPassword123",
            role=role,
            firebaseId="token",
        )


@pytest.mark.parametrize("role", ["moderator", "viewer", "superadmin"])
def test_social_auth_schema_rejects_staff_roles(role: str) -> None:
    with pytest.raises(ValidationError):
        SocialAuthRequest(
            loginType="google",
            firebaseId="token",
            user=role,
        )


def test_signup_and_social_schemas_accept_user_role_only() -> None:
    signup = EmailSignupRequest(
        firstName="Jane",
        lastName="Doe",
        email="jane@example.com",
        password="ValidPassword123",
        role="user",
        firebaseId="token",
    )
    assert signup.role == "user"

    social = SocialAuthRequest(loginType="google", firebaseId="token")
    assert social.user == "user"

    signup_schema = EmailSignupRequest.model_json_schema()
    social_schema = SocialAuthRequest.model_json_schema()

    signup_role = signup_schema["properties"]["role"]
    social_user = social_schema["properties"]["user"]
    # Pydantic may inline enum or $ref into $defs depending on version.
    signup_enum = signup_role.get("enum")
    social_enum = social_user.get("enum")
    if signup_enum is None:
        ref = signup_role.get("$ref", "").rsplit("/", 1)[-1]
        signup_enum = signup_schema.get("$defs", {}).get(ref, {}).get("enum")
    if social_enum is None:
        ref = social_user.get("$ref", "").rsplit("/", 1)[-1]
        social_enum = social_schema.get("$defs", {}).get(ref, {}).get("enum")
    assert signup_enum == ["user"]
    assert social_enum == ["user"]


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["moderator", "viewer", "superadmin"])
async def test_login_rejects_staff_user(role: str) -> None:
    # ensure_public_app_user checks loaded user.roles assignments, not User.role.
    staff_role = SimpleNamespace(name=role)
    user = SimpleNamespace(
        id=uuid4(),
        email="staff@example.com",
        firebase_uid="uid-staff",
        password_hash="hashed",
        registration_type=SimpleNamespace(value="email"),
        status=UserStatus.active,
        deleted_at=None,
        role=role,
        roles=[SimpleNamespace(role=staff_role)],
    )
    db = Mock()
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: user))

    payload = LoginRequest(
        email="staff@example.com",
        password="ValidPassword123",
        firebaseId="token",
    )
    with pytest.raises(ApiError, match="Account doesn't exist"):
        await auth_svc.login(
            payload,
            {"uid": "uid-staff", "email": "staff@example.com"},
            db,
        )


@pytest.mark.asyncio
async def test_signup_rejects_existing_staff_email_when_roles_not_loaded_on_user() -> None:
    """Staff block must use DB role assignment, not User.role property fallback."""
    staff_id = uuid4()
    existing = SimpleNamespace(
        id=staff_id,
        email="superadmin@kampulynk.com",
        firebase_uid=None,
        registration_type=RegistrationType.email,
        status=UserStatus.active,
        deleted_at=None,
        is_deleted=False,
        roles=[],
    )
    db = _mock_db()
    db.execute = AsyncMock(
        side_effect=[
            SimpleNamespace(scalar_one_or_none=lambda: None),
            SimpleNamespace(scalar_one_or_none=lambda: existing),
        ]
    )
    payload = EmailSignupRequest(
        firstName="Attacker",
        lastName="User",
        email="superadmin@kampulynk.com",
        password="ValidPassword123",
        role="user",
        firebaseId="token",
    )

    with (
        patch(
            "apps.accounts.services.registration_service.user_has_staff_role",
            new=AsyncMock(return_value=True),
        ) as staff_check,
        patch.object(reg_svc, "delete_firebase_user_safely"),
    ):
        with pytest.raises(ApiError, match="This user is not allowed"):
            await reg_svc.signup(
                payload,
                {"uid": "new-firebase-uid", "email": "superadmin@kampulynk.com"},
                db,
            )

    staff_check.assert_awaited_once_with(db, staff_id)


@pytest.mark.asyncio
async def test_signup_does_not_hijack_active_existing_email_account() -> None:
    existing = SimpleNamespace(
        id=uuid4(),
        email="user@example.com",
        firebase_uid="old-uid",
        registration_type=RegistrationType.email,
        status=UserStatus.active,
        deleted_at=None,
        is_deleted=False,
        roles=[],
    )
    db = _mock_db()
    db.execute = AsyncMock(
        side_effect=[
            SimpleNamespace(scalar_one_or_none=lambda: None),
            SimpleNamespace(scalar_one_or_none=lambda: existing),
        ]
    )
    payload = EmailSignupRequest(
        firstName="Attacker",
        lastName="User",
        email="user@example.com",
        password="ValidPassword123",
        role="user",
        firebaseId="token",
    )

    with patch(
        "apps.accounts.services.registration_service.user_has_staff_role",
        new=AsyncMock(return_value=False),
    ):
        with pytest.raises(ApiError, match="Account already exists"):
            await reg_svc.signup(
                payload,
                {"uid": "new-firebase-uid", "email": "user@example.com"},
                db,
            )


@pytest.mark.asyncio
async def test_signup_rejects_soft_deleted_email_within_grace_period() -> None:
    now = datetime.now(timezone.utc)
    existing = SimpleNamespace(
        id=uuid4(),
        email="user@example.com",
        firebase_uid="old-uid",
        registration_type=RegistrationType.email,
        status=UserStatus.deleting,
        deleted_at=now,
        is_deleted=True,
        purge_after=now + timedelta(days=30),
        password_hash=None,
        updated_at=now,
        last_login_at=None,
        roles=[],
    )
    db = _mock_db()
    db.execute = AsyncMock(
        side_effect=[
            SimpleNamespace(scalar_one_or_none=lambda: None),
            SimpleNamespace(scalar_one_or_none=lambda: existing),
        ]
    )

    payload = EmailSignupRequest(
        firstName="Returning",
        lastName="User",
        email="user@example.com",
        password="ValidPassword123",
        role="user",
        firebaseId="token",
    )

    with patch(
        "apps.accounts.services.registration_service.user_has_staff_role",
        new=AsyncMock(return_value=False),
    ):
        with pytest.raises(ApiError, match="Account already exists"):
            await reg_svc.signup(
                payload,
                {"uid": "new-firebase-uid", "email": "user@example.com"},
                db,
            )
