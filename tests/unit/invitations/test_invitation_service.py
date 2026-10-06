from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import Index

from apps.invitations.config import INVITATION_CODE_MAX_LENGTH
from apps.invitations.db_models import Invitation
from apps.invitations.services import invitation_service as svc
from common.enums import InvitationStatus


def _invitation(**kwargs):
    now = datetime.now(timezone.utc)
    defaults = {
        "id": uuid4(),
        "inviter_user_id": uuid4(),
        "code": "ABC1234",
        "status": InvitationStatus.active,
        "redemption_count": 0,
        "redeemed_by_user_id": None,
        "redeemed_at": None,
        "expires_at": now + timedelta(days=7),
        "is_active": True,
        "is_converted": False,
        "deactivated_by": None,
        "deleted_at": None,
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_invitation_owner_user_id_prefers_inviter_then_associator():
    generated = _invitation()
    assert svc.invitation_owner_user_id(generated) == generated.inviter_user_id

    associator_id = uuid4()
    associated = _invitation(inviter_user_id=None, redeemed_by_user_id=associator_id)
    assert svc.invitation_owner_user_id(associated) == associator_id
    code = svc.generate_invitation_code()
    assert len(code) == 7
    assert code[:3].isalpha() and code[:3].isupper()
    assert code[3:].isdigit()


def test_invitation_model_keeps_unique_code_index():
    index_names = {arg.name for arg in Invitation.__table_args__ if isinstance(arg, Index)}
    assert "ix_invitations_code" in index_names
    assert "ix_invitations_inviter_user_id" in index_names
    assert "ix_invitations_status" in index_names
    assert "ix_invitations_expires_at" in index_names
    assert "ix_invitations_deleted_at" in index_names
    assert "ix_invitations_inviter_created_at" in index_names
    assert "ix_invitations_redeemed_by_user_id" in index_names


@pytest.mark.asyncio
async def test_create_invitation_success(mock_db):
    db = mock_db()
    user_id = uuid4()
    created = _invitation(inviter_user_id=user_id, code="ABC1234")

    with (
        patch.object(svc, "lock_invitation_creation_daily_limit", AsyncMock()),
        patch.object(svc, "count_invitations_created_by_user_between", AsyncMock(return_value=2)),
        patch.object(svc, "invitation_code_exists", AsyncMock(return_value=False)),
        patch.object(svc, "persist_invitation", AsyncMock(return_value=created)) as persist,
        patch.object(svc, "generate_invitation_code", return_value="ABC1234"),
    ):
        response = await svc.create_invitation(db, user_id)

    assert response.status is True
    assert response.message == "Invitation code created successfully"
    assert response.data.code == "ABC1234"
    persist.assert_awaited_once()
    db.commit.assert_awaited_once()
    assert persist.await_args.kwargs["inviter_user_id"] == user_id
    assert persist.await_args.kwargs["code"] == "ABC1234"


@pytest.mark.asyncio
async def test_create_invitation_serializes_daily_limit_with_lock(mock_db):
    db = mock_db()
    user_id = uuid4()
    created = _invitation(inviter_user_id=user_id, code="ABC1234")
    order: list[str] = []

    async def _lock(*_args, **_kwargs):
        order.append("lock")

    async def _count(*_args, **_kwargs):
        order.append("count")
        return 0

    with (
        patch.object(svc, "lock_invitation_creation_daily_limit", side_effect=_lock),
        patch.object(svc, "count_invitations_created_by_user_between", side_effect=_count),
        patch.object(svc, "invitation_code_exists", AsyncMock(return_value=False)),
        patch.object(svc, "persist_invitation", AsyncMock(return_value=created)),
        patch.object(svc, "generate_invitation_code", return_value="ABC1234"),
    ):
        response = await svc.create_invitation(db, user_id)

    assert response.status is True
    assert order == ["lock", "count"]


@pytest.mark.asyncio
async def test_create_invitation_expires_at_uses_block_days(mock_db):
    db = mock_db()
    user_id = uuid4()
    now = datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc)
    created = _invitation(inviter_user_id=user_id, code="ABC1234")

    with (
        patch.object(svc, "lock_invitation_creation_daily_limit", AsyncMock()),
        patch.object(svc, "count_invitations_created_by_user_between", AsyncMock(return_value=0)),
        patch.object(svc, "invitation_code_exists", AsyncMock(return_value=False)),
        patch.object(svc, "persist_invitation", AsyncMock(return_value=created)) as persist,
        patch.object(svc, "generate_invitation_code", return_value="ABC1234"),
        patch.object(svc, "utc_now", return_value=now),
    ):
        response = await svc.create_invitation(db, user_id)

    assert response.status is True
    assert persist.await_args.kwargs["expires_at"] == now + timedelta(days=svc.settings.block_days)
    assert persist.await_args.kwargs["inviter_user_id"] == user_id
    assert "redeemed_by_user_id" not in persist.await_args.kwargs or persist.await_args.kwargs.get(
        "redeemed_by_user_id"
    ) is None


@pytest.mark.asyncio
async def test_associate_invitation_success(mock_db):
    db = mock_db()
    user_id = uuid4()
    created = _invitation(
        inviter_user_id=None,
        code="ABSCD",
        redeemed_by_user_id=user_id,
        redemption_count=0,
        redeemed_at=None,
        is_converted=False,
        status=InvitationStatus.active,
    )

    with (
        patch.object(svc, "invitation_code_exists", AsyncMock(return_value=False)),
        patch.object(svc, "_lock_user_association_daily_limit", AsyncMock()),
        patch.object(
            svc, "count_invitations_associated_by_user_between", AsyncMock(return_value=0)
        ),
        patch.object(svc, "persist_invitation", AsyncMock(return_value=created)) as persist,
    ):
        response = await svc.associate_invitation(db, user_id, "ABSCD")

    assert response.status is True
    assert response.message == "Invitation code associated successfully"
    assert response.data.code == "ABSCD"
    assert response.data.status == "ACTIVE"
    assert response.data.redeemed_by_user_id == user_id
    persist.assert_awaited_once()
    kwargs = persist.await_args.kwargs
    assert kwargs["inviter_user_id"] is None
    assert kwargs["code"] == "ABSCD"
    assert kwargs["status"] == InvitationStatus.active
    assert kwargs["redeemed_by_user_id"] == user_id
    db.commit.assert_awaited_once()


@pytest.mark.parametrize(
    "code",
    ["ABSCD", "google2026", "123456", "INVITE-USER-123", "ABC-XYZ-999"],
)
@pytest.mark.asyncio
async def test_associate_invitation_accepts_arbitrary_codes(mock_db, code):
    db = mock_db()
    user_id = uuid4()
    created = _invitation(
        inviter_user_id=None,
        code=code.upper(),
        redeemed_by_user_id=user_id,
    )

    with (
        patch.object(svc, "invitation_code_exists", AsyncMock(return_value=False)),
        patch.object(svc, "_lock_user_association_daily_limit", AsyncMock()),
        patch.object(
            svc, "count_invitations_associated_by_user_between", AsyncMock(return_value=0)
        ),
        patch.object(svc, "persist_invitation", AsyncMock(return_value=created)) as persist,
    ):
        response = await svc.associate_invitation(db, user_id, code)

    assert response.status is True
    assert persist.await_args.kwargs["code"] == code.upper()


@pytest.mark.asyncio
async def test_associate_invitation_rejects_duplicate_code(mock_db):
    db = mock_db()
    with patch.object(svc, "invitation_code_exists", AsyncMock(return_value=True)):
        response = await svc.associate_invitation(db, uuid4(), "ABSCD")

    assert response.status is False
    assert response.message == "This code is used, please generate a new code"
    assert response.data is None
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_associate_invitation_rejects_empty_code(mock_db):
    db = mock_db()
    response = await svc.associate_invitation(db, uuid4(), "   ")
    assert response.status is False
    assert response.message == "Invalid or expired invitation code"


@pytest.mark.asyncio
async def test_associate_invitation_rejects_code_exceeding_max_length(mock_db):
    db = mock_db()
    response = await svc.associate_invitation(
        db, uuid4(), "X" * (INVITATION_CODE_MAX_LENGTH + 1)
    )
    assert response.status is False
    assert response.message == "Invalid or expired invitation code"


@pytest.mark.asyncio
async def test_associate_invitation_allows_up_to_daily_limit(mock_db):
    db = mock_db()
    user_id = uuid4()
    created = _invitation(inviter_user_id=None, code="CODE50", redeemed_by_user_id=user_id)
    limit = svc.settings.daily_limit

    with (
        patch.object(svc, "invitation_code_exists", AsyncMock(return_value=False)),
        patch.object(svc, "_lock_user_association_daily_limit", AsyncMock()),
        patch.object(
            svc,
            "count_invitations_associated_by_user_between",
            AsyncMock(return_value=limit - 1),
        ),
        patch.object(svc, "persist_invitation", AsyncMock(return_value=created)) as persist,
        patch.object(
            svc, "count_invitations_created_by_user_between", AsyncMock()
        ) as generated_count,
    ):
        response = await svc.associate_invitation(db, user_id, "CODE50")

    assert response.status is True
    persist.assert_awaited_once()
    generated_count.assert_not_called()


@pytest.mark.asyncio
async def test_associate_invitation_daily_limit_reached_does_not_insert(mock_db):
    db = mock_db()
    user_id = uuid4()
    limit = svc.settings.daily_limit

    with (
        patch.object(svc, "invitation_code_exists", AsyncMock(return_value=False)),
        patch.object(svc, "_lock_user_association_daily_limit", AsyncMock()) as lock,
        patch.object(
            svc,
            "count_invitations_associated_by_user_between",
            AsyncMock(return_value=limit),
        ) as count,
        patch.object(svc, "persist_invitation", AsyncMock()) as persist,
    ):
        response = await svc.associate_invitation(db, user_id, "CODE51")

    assert response.status is False
    assert response.message == "Daily invitation limit reached"
    assert response.data is None
    persist.assert_not_called()
    db.commit.assert_not_called()
    db.rollback.assert_awaited_once()
    lock.assert_awaited_once()
    count.assert_awaited_once()
    assert count.await_args.args[1] == user_id


@pytest.mark.asyncio
async def test_associate_invitation_resets_on_next_calendar_day(mock_db):
    db = mock_db()
    user_id = uuid4()
    created = _invitation(inviter_user_id=None, code="NEXTDAY", redeemed_by_user_id=user_id)
    day_start = datetime(2026, 9, 12, 4, 0, tzinfo=timezone.utc)
    day_end = datetime(2026, 9, 13, 4, 0, tzinfo=timezone.utc)

    with (
        patch.object(svc, "invitation_code_exists", AsyncMock(return_value=False)),
        patch.object(svc, "_lock_user_association_daily_limit", AsyncMock()),
        patch.object(svc, "utc_day_bounds", return_value=(day_start, day_end)) as bounds,
        patch.object(
            svc,
            "count_invitations_associated_by_user_between",
            AsyncMock(return_value=0),
        ) as count,
        patch.object(svc, "persist_invitation", AsyncMock(return_value=created)) as persist,
    ):
        response = await svc.associate_invitation(db, user_id, "NEXTDAY")

    assert response.status is True
    persist.assert_awaited_once()
    bounds.assert_called_once()
    count.assert_awaited_once_with(db, user_id, start_at=day_start, end_at=day_end)


def test_utc_day_bounds_uses_analytics_timezone():
    from common.timezone_settings import app_timezone_settings

    now = datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc)
    with patch.object(svc, "calendar_day_bounds_utc") as calendar_bounds:
        calendar_bounds.return_value = (
            datetime(2026, 9, 11, 4, 0, tzinfo=timezone.utc),
            datetime(2026, 9, 12, 4, 0, tzinfo=timezone.utc),
        )
        svc.utc_day_bounds(now)

    calendar_bounds.assert_called_once_with(app_timezone_settings.timezone, now=now)
    assert svc.settings.timezone == app_timezone_settings.timezone


@pytest.mark.asyncio
async def test_associate_invitation_expires_at_uses_block_days(mock_db):
    db = mock_db()
    user_id = uuid4()
    now = datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc)
    created = _invitation(inviter_user_id=None, code="ABSCD", redeemed_by_user_id=user_id)
    block_days = svc.settings.block_days

    with (
        patch.object(svc, "invitation_code_exists", AsyncMock(return_value=False)),
        patch.object(svc, "_lock_user_association_daily_limit", AsyncMock()),
        patch.object(
            svc, "count_invitations_associated_by_user_between", AsyncMock(return_value=0)
        ),
        patch.object(svc, "persist_invitation", AsyncMock(return_value=created)) as persist,
        patch.object(svc, "utc_now", return_value=now),
    ):
        response = await svc.associate_invitation(db, user_id, "ABSCD")

    assert response.status is True
    assert persist.await_args.kwargs["expires_at"] == now + timedelta(days=block_days)
    assert persist.await_args.kwargs["status"] == InvitationStatus.active


@pytest.mark.asyncio
async def test_associate_invitation_serializes_daily_limit_with_lock(mock_db):
    db = mock_db()
    user_id = uuid4()
    created = _invitation(inviter_user_id=None, code="LOCK1", redeemed_by_user_id=user_id)
    order: list[str] = []

    async def _lock(*_args, **_kwargs):
        order.append("lock")

    async def _count(*_args, **_kwargs):
        order.append("count")
        return 0

    async def _persist(*_args, **_kwargs):
        order.append("persist")
        return created

    with (
        patch.object(svc, "invitation_code_exists", AsyncMock(return_value=False)),
        patch.object(svc, "_lock_user_association_daily_limit", side_effect=_lock),
        patch.object(svc, "count_invitations_associated_by_user_between", side_effect=_count),
        patch.object(svc, "persist_invitation", side_effect=_persist),
    ):
        response = await svc.associate_invitation(db, user_id, "LOCK1")

    assert response.status is True
    assert order == ["lock", "count", "persist"]


@pytest.mark.asyncio
async def test_associate_duplicate_is_checked_before_daily_limit(mock_db):
    db = mock_db()
    with (
        patch.object(svc, "invitation_code_exists", AsyncMock(return_value=True)),
        patch.object(svc, "_lock_user_association_daily_limit", AsyncMock()) as lock,
        patch.object(
            svc, "count_invitations_associated_by_user_between", AsyncMock()
        ) as count,
        patch.object(svc, "persist_invitation", AsyncMock()) as persist,
    ):
        response = await svc.associate_invitation(db, uuid4(), "ABSCD")

    assert response.status is False
    assert response.message == "This code is used, please generate a new code"
    lock.assert_not_called()
    count.assert_not_called()
    persist.assert_not_called()


@pytest.mark.asyncio
async def test_create_invitation_daily_limit(mock_db):
    db = mock_db()
    with (
        patch.object(svc, "lock_invitation_creation_daily_limit", AsyncMock()) as lock,
        patch.object(
            svc,
            "count_invitations_created_by_user_between",
            AsyncMock(return_value=svc.settings.daily_limit),
        ),
    ):
        response = await svc.create_invitation(db, uuid4())

    assert response.status is False
    assert response.message == "Daily invitation limit reached"
    assert response.data is None
    lock.assert_awaited_once()
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_validate_invitation_returns_active_status(mock_db):
    db = mock_db()
    invitation = _invitation(code="ABSCD", status=InvitationStatus.active)

    with patch.object(svc, "get_invitation_by_code", AsyncMock(return_value=invitation)):
        response = await svc.validate_invitation(db, "ABSCD")

    assert response.status is True
    assert response.message == "success"
    assert response.data.code == "ABSCD"
    assert response.data.status == "ACTIVE"
    db.add.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_validate_invitation_returns_expired_status(mock_db):
    db = mock_db()
    invitation = _invitation(code="ABSCD", status=InvitationStatus.expired)
    with patch.object(svc, "get_invitation_by_code", AsyncMock(return_value=invitation)):
        response = await svc.validate_invitation(db, "ABSCD")

    assert response.status is True
    assert response.data.status == "EXPIRED"


@pytest.mark.asyncio
async def test_validate_invitation_returns_redeemed_status(mock_db):
    db = mock_db()
    invitation = _invitation(code="ABSCD", status=InvitationStatus.redeemed)
    with patch.object(svc, "get_invitation_by_code", AsyncMock(return_value=invitation)):
        response = await svc.validate_invitation(db, "ABSCD")

    assert response.status is True
    assert response.data.status == "REDEEMED"


@pytest.mark.asyncio
async def test_validate_invitation_returns_deactivated_status(mock_db):
    db = mock_db()
    invitation = _invitation(code="ABSCD", status=InvitationStatus.deactivated)
    with patch.object(svc, "get_invitation_by_code", AsyncMock(return_value=invitation)):
        response = await svc.validate_invitation(db, "ABSCD")

    assert response.status is True
    assert response.data.status == "DEACTIVATED"


@pytest.mark.asyncio
async def test_validate_invitation_returns_expired_when_expires_at_passed(mock_db):
    db = mock_db()
    invitation = _invitation(
        code="ABSCD",
        status=InvitationStatus.active,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        redemption_count=0,
        is_converted=False,
    )
    with patch.object(svc, "get_invitation_by_code", AsyncMock(return_value=invitation)):
        response = await svc.validate_invitation(db, "ABSCD")

    assert response.status is True
    assert response.data.code == "ABSCD"
    assert response.data.status == "EXPIRED"
    db.add.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_validate_invitation_unknown_code(mock_db):
    db = mock_db()
    with patch.object(svc, "get_invitation_by_code", AsyncMock(return_value=None)):
        response = await svc.validate_invitation(db, "NOPE99")

    assert response.status is False
    assert response.message == "Invalid or expired invitation code"
    assert response.data is None


@pytest.mark.asyncio
async def test_redeem_invitation_success(mock_db):
    db = mock_db()
    invitation = _invitation()
    redeemer_id = uuid4()

    with patch.object(svc, "get_invitation_by_code_for_update", AsyncMock(return_value=invitation)):
        result = await svc.redeem_invitation(
            db,
            code=invitation.code,
            redeemed_by_user_id=redeemer_id,
            commit=False,
        )

    assert result.redeemed_by_user_id == redeemer_id
    assert result.redemption_count == 1
    assert result.is_converted is True
    assert result.status == InvitationStatus.redeemed
    db.commit.assert_not_called()

    # Verify auto-connection additions
    added_objects = [call[0][0] for call in db.add.call_args_list]
    assert any(obj.__class__.__name__ == "ConnectionRequest" for obj in added_objects)
    assert any(obj.__class__.__name__ == "Connection" for obj in added_objects)


@pytest.mark.asyncio
async def test_redeem_invitation_rejects_self_redeem(mock_db):
    db = mock_db()
    invitation = _invitation()

    with patch.object(svc, "get_invitation_by_code_for_update", AsyncMock(return_value=invitation)):
        with pytest.raises(HTTPException) as exc:
            await svc.redeem_invitation(
                db,
                code=invitation.code,
                redeemed_by_user_id=invitation.inviter_user_id,
            )

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_redeem_associated_invitation_connects_associating_user(mock_db):
    db = mock_db()
    associator_id = uuid4()
    onboarded_id = uuid4()
    invitation = _invitation(
        inviter_user_id=None,
        code="ABSCD",
        redeemed_by_user_id=associator_id,
        redemption_count=0,
        redeemed_at=None,
        is_converted=False,
        status=InvitationStatus.active,
    )

    with (
        patch.object(svc, "get_invitation_by_code_for_update", AsyncMock(return_value=invitation)),
        patch.object(svc, "connect_users_from_invitation", AsyncMock()) as connect,
    ):
        result = await svc.redeem_invitation(
            db,
            code="ABSCD",
            redeemed_by_user_id=onboarded_id,
            commit=False,
        )

    assert result.redeemed_by_user_id == onboarded_id
    assert result.redemption_count == 1
    assert result.redeemed_at is not None
    assert result.is_converted is True
    assert result.status == InvitationStatus.redeemed
    connect.assert_awaited_once_with(
        db,
        owner_user_id=associator_id,
        redeemed_by_user_id=onboarded_id,
    )


@pytest.mark.asyncio
async def test_redeem_associated_invitation_rejects_self_redeem(mock_db):
    db = mock_db()
    user_id = uuid4()
    invitation = _invitation(
        inviter_user_id=None,
        code="ABSCD",
        redeemed_by_user_id=user_id,
        redemption_count=0,
        redeemed_at=None,
        is_converted=False,
        status=InvitationStatus.active,
    )

    with patch.object(svc, "get_invitation_by_code_for_update", AsyncMock(return_value=invitation)):
        with pytest.raises(HTTPException) as exc:
            await svc.redeem_invitation(
                db,
                code="ABSCD",
                redeemed_by_user_id=user_id,
            )

    assert exc.value.status_code == 400
    assert exc.value.detail == "You cannot redeem your own invitation code"


@pytest.mark.asyncio
async def test_redeem_generated_invitation_uses_shared_connect_helper(mock_db):
    db = mock_db()
    invitation = _invitation()
    redeemer_id = uuid4()

    with (
        patch.object(svc, "get_invitation_by_code_for_update", AsyncMock(return_value=invitation)),
        patch.object(svc, "connect_users_from_invitation", AsyncMock()) as connect,
    ):
        result = await svc.redeem_invitation(
            db,
            code=invitation.code,
            redeemed_by_user_id=redeemer_id,
            commit=False,
        )

    assert result.status == InvitationStatus.redeemed
    connect.assert_awaited_once_with(
        db,
        owner_user_id=invitation.inviter_user_id,
        redeemed_by_user_id=redeemer_id,
    )


@pytest.mark.asyncio
async def test_complete_onboarding_redeems_invitation(monkeypatch):
    from unittest.mock import MagicMock

    from common.enums import UserStatus
    from apps.profiles.db_models import Profile
    from apps.profiles.services.onboarding_service import complete_onboarding

    user = MagicMock()
    user.id = uuid4()
    user.email = "onboard-invite@example.com"
    user.onboarding_status = None
    user.status = UserStatus.pending
    user.referred_by_user_id = None

    inviter_id = uuid4()
    invitation = _invitation(inviter_user_id=inviter_id, code="ABC1234")
    redeem = AsyncMock(return_value=invitation)

    profile = Profile(user_id=user.id, first_name="", last_name="", completeness_score=0)
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(
        side_effect=[
            MagicMock(scalar_one_or_none=MagicMock(return_value=profile)),
        ]
    )
    mock_db.refresh = AsyncMock()

    monkeypatch.setattr("core.images.file_exists", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        "apps.profiles.services.onboarding_service._resolve_catalog_program",
        AsyncMock(side_effect=[(1, "Physics"), (None, None)]),
    )
    monkeypatch.setattr(
        "apps.profiles.services.onboarding_service._resolve_academic_interest_ids",
        AsyncMock(return_value=[1]),
    )
    monkeypatch.setattr(
        "apps.profiles.services.onboarding_service.calculate_completeness_score",
        AsyncMock(return_value=80),
    )
    monkeypatch.setattr(
        "apps.recommendations.services.post_keyword_service.refresh_profile_extracted_keywords_best_effort",
        AsyncMock(),
    )
    monkeypatch.setattr("apps.chat.service.sync_stream_user_on_auth", AsyncMock())
    monkeypatch.setattr(
        "apps.notifications.services.topic_service.TopicService.refresh_user_topic_subscriptions",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "apps.profiles.services.onboarding_service.build_user_base_response",
        AsyncMock(return_value={"email": user.email}),
    )
    monkeypatch.setattr("apps.invitations.services.redeem_invitation", redeem)

    await complete_onboarding(
        user=user,
        bio="Bio",
        major="Physics",
        minor=None,
        country_id=None,
        university_id=str(uuid4()),
        education_level_id=2,
        academic_interests=["Math"],
        profile_photo_key="profiles/test.png",
        banner_photo_key=None,
        db=mock_db,
        invitation_code="ABC1234",
    )

    redeem.assert_awaited_once()
    assert redeem.await_args.kwargs["code"] == "ABC1234"
    assert redeem.await_args.kwargs["redeemed_by_user_id"] == user.id
    assert redeem.await_args.kwargs["commit"] is False
    assert user.referred_by_user_id == inviter_id
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_complete_onboarding_skips_lynkup_for_invalid_invitation(monkeypatch):
    """Expired/redeemed/invalid invitation codes must not fail onboarding."""
    from unittest.mock import MagicMock

    from fastapi import HTTPException, status

    from common.enums import OnboardingStatus, UserStatus
    from apps.profiles.db_models import Profile
    from apps.profiles.services.onboarding_service import complete_onboarding

    user = MagicMock()
    user.id = uuid4()
    user.email = "onboard-expired-invite@example.com"
    user.onboarding_status = None
    user.status = UserStatus.pending
    user.referred_by_user_id = None

    redeem = AsyncMock(
        side_effect=HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired invitation code",
        )
    )

    profile = Profile(user_id=user.id, first_name="", last_name="", completeness_score=0)
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(
        side_effect=[
            MagicMock(scalar_one_or_none=MagicMock(return_value=profile)),
        ]
    )
    mock_db.refresh = AsyncMock()

    monkeypatch.setattr("core.images.file_exists", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        "apps.profiles.services.onboarding_service._resolve_catalog_program",
        AsyncMock(side_effect=[(1, "Physics"), (None, None)]),
    )
    monkeypatch.setattr(
        "apps.profiles.services.onboarding_service._resolve_academic_interest_ids",
        AsyncMock(return_value=[1]),
    )
    monkeypatch.setattr(
        "apps.profiles.services.onboarding_service.calculate_completeness_score",
        AsyncMock(return_value=80),
    )
    monkeypatch.setattr(
        "apps.recommendations.services.post_keyword_service.refresh_profile_extracted_keywords_best_effort",
        AsyncMock(),
    )
    monkeypatch.setattr("apps.chat.service.sync_stream_user_on_auth", AsyncMock())
    monkeypatch.setattr(
        "apps.notifications.services.topic_service.TopicService.refresh_user_topic_subscriptions",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "apps.profiles.services.onboarding_service.build_user_base_response",
        AsyncMock(return_value={"email": user.email}),
    )
    monkeypatch.setattr("apps.invitations.services.redeem_invitation", redeem)

    result = await complete_onboarding(
        user=user,
        bio="Bio",
        major="Physics",
        minor=None,
        country_id=None,
        university_id=str(uuid4()),
        education_level_id=2,
        academic_interests=["Math"],
        profile_photo_key="profiles/test.png",
        banner_photo_key=None,
        db=mock_db,
        invitation_code="KC1Pi87rD6b",
    )

    redeem.assert_awaited_once()
    assert user.referred_by_user_id is None
    assert user.onboarding_status == OnboardingStatus.completed
    mock_db.commit.assert_awaited_once()
    assert result["user"]["email"] == user.email


@pytest.mark.asyncio
async def test_get_all_invitations_paginated(mock_db):
    db = mock_db()
    inviter_id = uuid4()
    invitation = _invitation(inviter_user_id=inviter_id, code="XYZ9876")
    user = SimpleNamespace(id=inviter_id)
    profile = SimpleNamespace(first_name="Sahil", last_name="Deshpande")

    summary = {
        "active": 1,
        "redeemed": 0,
        "deleted": 0,
        "expired": 0,
        "total": 1,
    }
    with (
        patch.object(svc, "count_invitations", AsyncMock(return_value=1)),
        patch.object(
            svc,
            "count_invitations_status_summary",
            AsyncMock(return_value=summary),
        ),
        patch.object(
            svc,
            "list_invitations_with_inviter",
            AsyncMock(return_value=[(invitation, user, profile)]),
        ) as list_mock,
    ):
        response = await svc.get_all_invitations(db, page=1, page_size=20)

    list_mock.assert_awaited_once_with(db, page=1, page_size=20, search=None, status=None)
    assert response.status is True
    assert response.message == "Invitation codes fetched successfully"
    assert response.data["totalItems"] == 1
    assert response.data["page"] == 1
    assert response.data["pageSize"] == 20
    assert response.data["totalPages"] == 1
    assert response.data["summary"] == summary
    item = response.data["items"][0]
    assert item["code"] == "XYZ9876"
    assert item["user_id"] == inviter_id
    assert item["first_name"] == "Sahil"
    assert item["last_name"] == "Deshpande"
    assert item["username"] == "Sahil Deshpande"
    assert item["status"] == "Active"
    assert item["deleted_at"] is None
    assert "created_by" not in item


@pytest.mark.asyncio
async def test_get_all_invitations_without_pagination_returns_all(mock_db):
    db = mock_db()
    inviter_id = uuid4()
    invitation = _invitation(inviter_user_id=inviter_id, code="XYZ9876")
    user = SimpleNamespace(id=inviter_id)
    profile = SimpleNamespace(first_name="Sahil", last_name="Deshpande")

    with (
        patch.object(svc, "count_invitations", AsyncMock(return_value=1)),
        patch.object(
            svc,
            "count_invitations_status_summary",
            AsyncMock(return_value={"active": 1, "total": 1}),
        ),
        patch.object(
            svc,
            "list_invitations_with_inviter",
            AsyncMock(return_value=[(invitation, user, profile)]),
        ) as list_mock,
    ):
        response = await svc.get_all_invitations(db, page=None, page_size=None)

    list_mock.assert_awaited_once_with(db, page=None, page_size=None, search=None, status=None)
    assert response.data["page"] == 1
    assert response.data["pageSize"] == 1
    assert response.data["totalItems"] == 1
    assert len(response.data["items"]) == 1


@pytest.mark.asyncio
async def test_get_all_invitations_forwards_search(mock_db):
    db = mock_db()

    with (
        patch.object(svc, "count_invitations", AsyncMock(return_value=0)) as count_mock,
        patch.object(
            svc,
            "count_invitations_status_summary",
            AsyncMock(return_value={"active": 0, "total": 0}),
        ),
        patch.object(
            svc,
            "list_invitations_with_inviter",
            AsyncMock(return_value=[]),
        ) as list_mock,
    ):
        response = await svc.get_all_invitations(
            db, page=1, page_size=10, search="XYZ"
        )

    count_mock.assert_awaited_once_with(db, search="XYZ", status=None)
    list_mock.assert_awaited_once_with(
        db, page=1, page_size=10, search="XYZ", status=None
    )
    assert response.status is True
    assert response.data["totalItems"] == 0


@pytest.mark.asyncio
async def test_get_all_invitations_forwards_status(mock_db):
    db = mock_db()

    with (
        patch.object(svc, "count_invitations", AsyncMock(return_value=0)) as count_mock,
        patch.object(
            svc,
            "count_invitations_status_summary",
            AsyncMock(return_value={"active": 0, "total": 0}),
        ),
        patch.object(
            svc,
            "list_invitations_with_inviter",
            AsyncMock(return_value=[]),
        ) as list_mock,
    ):
        response = await svc.get_all_invitations(
            db, page=1, page_size=10, search=None, status="Active"
        )

    count_mock.assert_awaited_once_with(db, search=None, status="Active")
    list_mock.assert_awaited_once_with(
        db, page=1, page_size=10, search=None, status="Active"
    )
    assert response.status is True


def test_admin_list_invitation_status_reflects_effective_state():
    now = datetime.now(timezone.utc)
    active = _invitation(expires_at=now + timedelta(days=1))
    expired = _invitation(
        status=InvitationStatus.active,
        expires_at=now - timedelta(days=1),
    )
    stored_expired = _invitation(status=InvitationStatus.expired)
    redeemed = _invitation(status=InvitationStatus.redeemed)
    deleted = _invitation(deleted_at=now, status=InvitationStatus.deactivated)

    assert svc.admin_list_invitation_status(active, now=now) == "Active"
    assert svc.admin_list_invitation_status(expired, now=now) == "Expired"
    assert svc.admin_list_invitation_status(stored_expired, now=now) == "Expired"
    assert svc.admin_list_invitation_status(redeemed, now=now) == "Redeemed"
    assert svc.admin_list_invitation_status(deleted, now=now) == "Deleted"


def test_invitation_status_filter_clause_builds_expected_predicates():
    from apps.invitations.repositories.invitation_repository import (
        _invitation_status_filter_clause,
    )
    from common.enums import AdminInvitationListStatus

    assert _invitation_status_filter_clause(None) is None
    assert _invitation_status_filter_clause("not-a-status") is None

    active = _invitation_status_filter_clause(AdminInvitationListStatus.active)
    redeemed = _invitation_status_filter_clause(AdminInvitationListStatus.redeemed)
    deleted = _invitation_status_filter_clause(AdminInvitationListStatus.deleted)
    expired = _invitation_status_filter_clause(AdminInvitationListStatus.expired)

    assert active is not None
    assert redeemed is not None
    assert deleted is not None
    assert expired is not None


def test_invitation_code_search_clause_matches_code_and_name():
    from sqlalchemy.dialects import postgresql

    from apps.invitations.repositories.invitation_repository import (
        _invitation_search_clause,
    )

    assert _invitation_search_clause(None) is None
    assert _invitation_search_clause("   ") is None
    clause = _invitation_search_clause("XYZ")
    sql = str(
        clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    ).lower()
    assert "lower" in sql
    assert "like" in sql
    assert "%xyz%" in sql
    assert "code" in sql
    assert "first_name" in sql
    assert "last_name" in sql



@pytest.mark.asyncio
async def test_soft_delete_invitation_success(mock_db):
    db = mock_db()
    invitation = _invitation()
    admin_id = uuid4()

    with patch.object(svc, "get_invitation_by_code_for_update", AsyncMock(return_value=invitation)):
        response = await svc.soft_delete_invitation(
            db,
            code=invitation.code,
            admin_user_id=admin_id,
        )

    assert response.status is True
    assert response.message == "Invitation code deleted successfully"
    assert response.data == {}
    assert invitation.deleted_at is not None
    assert invitation.status == InvitationStatus.deactivated
    assert invitation.is_active is False
    assert invitation.deactivated_by == admin_id
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_soft_delete_invitation_not_found(mock_db):
    db = mock_db()

    with patch.object(svc, "get_invitation_by_code_for_update", AsyncMock(return_value=None)):
        response = await svc.soft_delete_invitation(
            db,
            code="ZZZ0000",
            admin_user_id=uuid4(),
        )

    assert response.status is False
    assert response.message == "Invitation code not found"
    assert response.data == {}


def test_is_invitation_currently_valid_rejects_inactive_expired_deleted():
    now = datetime.now(timezone.utc)
    active = _invitation()
    assert svc.is_invitation_currently_valid(active, now=now) is True

    assert svc.is_invitation_currently_valid(_invitation(is_active=False), now=now) is False
    assert svc.is_invitation_currently_valid(
        _invitation(status=InvitationStatus.deactivated), now=now
    ) is False
    assert svc.is_invitation_currently_valid(
        _invitation(expires_at=now - timedelta(seconds=1)), now=now
    ) is False
    assert svc.is_invitation_currently_valid(
        _invitation(deleted_at=now), now=now
    ) is False
    assert svc.is_invitation_currently_valid(
        _invitation(redemption_count=1), now=now
    ) is False


@pytest.mark.asyncio
async def test_redeem_invitation_rejects_invalid_code(mock_db):
    db = mock_db()
    with patch.object(svc, "get_invitation_by_code_for_update", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc:
            await svc.redeem_invitation(db, code="nope", redeemed_by_user_id=uuid4())
    assert exc.value.status_code == 400
    assert exc.value.detail == "Invalid or expired invitation code"


@pytest.mark.asyncio
async def test_redeem_invitation_rejects_expired_code(mock_db):
    db = mock_db()
    invitation = _invitation(expires_at=datetime.now(timezone.utc) - timedelta(days=1))
    with patch.object(svc, "get_invitation_by_code_for_update", AsyncMock(return_value=invitation)):
        with pytest.raises(HTTPException) as exc:
            await svc.redeem_invitation(db, code=invitation.code, redeemed_by_user_id=uuid4())
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_redeem_invitation_rejects_inactive_code(mock_db):
    db = mock_db()
    invitation = _invitation(is_active=False, status=InvitationStatus.deactivated)
    with patch.object(svc, "get_invitation_by_code_for_update", AsyncMock(return_value=invitation)):
        with pytest.raises(HTTPException) as exc:
            await svc.redeem_invitation(db, code=invitation.code, redeemed_by_user_id=uuid4())
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_redeem_invitation_rejects_deleted_code(mock_db):
    db = mock_db()
    invitation = _invitation(deleted_at=datetime.now(timezone.utc))
    with patch.object(svc, "get_invitation_by_code_for_update", AsyncMock(return_value=invitation)):
        with pytest.raises(HTTPException) as exc:
            await svc.redeem_invitation(db, code=invitation.code, redeemed_by_user_id=uuid4())
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_redeem_invitation_prevents_duplicate(mock_db):
    db = mock_db()
    invitation = _invitation(redemption_count=1, is_converted=True, status=InvitationStatus.redeemed)
    with patch.object(svc, "get_invitation_by_code_for_update", AsyncMock(return_value=invitation)):
        with pytest.raises(HTTPException) as exc:
            await svc.redeem_invitation(db, code=invitation.code, redeemed_by_user_id=uuid4())
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_redeem_invitation_response_success(mock_db):
    db = mock_db()
    invitation = _invitation()
    redeemer_id = uuid4()
    with patch.object(svc, "redeem_invitation", AsyncMock(return_value=invitation)) as redeem:
        response = await svc.redeem_invitation_response(
            db,
            code=invitation.code,
            redeemed_by_user_id=redeemer_id,
        )

    assert response.status is True
    assert response.message == "Invitation redeemed successfully"
    assert response.data.code == invitation.code
    redeem.assert_awaited_once()
    assert redeem.await_args.kwargs["commit"] is True


@pytest.mark.asyncio
async def test_redeem_invitation_response_invalid_code(mock_db):
    db = mock_db()
    with patch.object(
        svc,
        "redeem_invitation",
        AsyncMock(side_effect=HTTPException(status_code=400, detail="Invalid or expired invitation code")),
    ):
        response = await svc.redeem_invitation_response(
            db,
            code="missing",
            redeemed_by_user_id=uuid4(),
        )

    assert response.status is False
    assert response.message == "Invalid or expired invitation code"
    assert response.data is None


@pytest.mark.asyncio
async def test_redeem_invitation_response_connection_failure(mock_db):
    db = mock_db()
    with patch.object(
        svc,
        "redeem_invitation",
        AsyncMock(side_effect=RuntimeError("conn fail")),
    ):
        response = await svc.redeem_invitation_response(
            db,
            code="ABC1234",
            redeemed_by_user_id=uuid4(),
        )

    assert response.status is False
    assert response.message == "Failed to redeem invitation"
    assert response.data is None
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_redeem_rolls_back_when_connection_creation_fails(mock_db):
    db = mock_db()
    invitation = _invitation()
    with (
        patch.object(svc, "get_invitation_by_code_for_update", AsyncMock(return_value=invitation)),
        patch.object(svc, "connect_users_from_invitation", AsyncMock(side_effect=RuntimeError("conn fail"))),
    ):
        with pytest.raises(RuntimeError, match="conn fail"):
            await svc.redeem_invitation(
                db,
                code=invitation.code,
                redeemed_by_user_id=uuid4(),
                commit=True,
            )

    db.rollback.assert_awaited_once()
    db.commit.assert_not_called()
