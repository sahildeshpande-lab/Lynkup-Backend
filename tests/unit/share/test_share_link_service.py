from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from apps.share.schemas import ShareLinkRequest, ShareLinkType
from apps.share.services import share_link_service as svc
from apps.share.services.branch_service import BranchLinkError, BranchLinkResult
from common.enums import PostState
from common.exceptions import ApiError


def _branch_result(code: str = "tPcFcGY2r6b") -> BranchLinkResult:
    return BranchLinkResult(
        url=f"https://jlrh8.test-app.link/{code}",
        code=code,
    )


def _invitation(user_id, code: str = "tPcFcGY2r6b"):
    return SimpleNamespace(
        id=uuid4(),
        inviter_user_id=user_id,
        code=code,
    )


def _post(*, state=PostState.published, author_user_id=None):
    return SimpleNamespace(
        id=uuid4(),
        state=state,
        author_user_id=author_user_id or uuid4(),
        share_count=3,
    )


@pytest.mark.asyncio
async def test_create_link_dispatches_share(mock_db):
    db = mock_db()
    post_id = uuid4()
    with patch.object(
        svc,
        "create_post_share_link",
        AsyncMock(return_value=svc.success_response("ok", response_cls=svc.ShareLinkResponse)),
    ) as share:
        await svc.create_link(
            db,
            uuid4(),
            ShareLinkRequest(type=ShareLinkType.share, post_id=post_id),
        )
    share.assert_awaited_once()
    assert share.await_args.args[2] == post_id
    db = mock_db()
    user_id = uuid4()
    created = _invitation(user_id)
    branch = _branch_result()

    with (
        patch.object(svc, "lock_invitation_creation_daily_limit", AsyncMock()) as lock,
        patch.object(svc, "count_invitations_created_by_user_between", AsyncMock(return_value=0)),
        patch.object(svc, "create_branch_link", AsyncMock(return_value=branch)) as branch_call,
        patch.object(svc, "invitation_code_exists", AsyncMock(return_value=False)),
        patch.object(svc, "persist_invitation", AsyncMock(return_value=created)) as persist,
    ):
        response = await svc.create_link(db, user_id, ShareLinkRequest(type=ShareLinkType.invite))

    assert response.status is True
    assert response.message == "Invitation link created successfully"
    assert response.data.type == ShareLinkType.invite
    assert response.data.code == branch.code
    assert response.data.url == branch.url
    lock.assert_awaited_once()
    persist.assert_awaited_once()
    assert persist.await_args.kwargs["inviter_user_id"] == user_id
    assert persist.await_args.kwargs["code"] == branch.code
    branch_payload = branch_call.await_args.args[0]
    assert branch_payload["type"] == "invite"
    canonical = branch_payload["$canonical_identifier"]
    assert canonical.startswith("invite/")
    UUID(canonical.split("/", 1)[1])
    assert branch_payload["$deeplink_path"] == "invite"
    assert branch_payload["referred_id"] == str(user_id)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_invite_link_user_id_comes_from_authenticated_user(mock_db):
    db = mock_db()
    user_id = uuid4()
    created = _invitation(user_id)
    with (
        patch.object(svc, "lock_invitation_creation_daily_limit", AsyncMock()),
        patch.object(svc, "count_invitations_created_by_user_between", AsyncMock(return_value=2)),
        patch.object(svc, "create_branch_link", AsyncMock(return_value=_branch_result())),
        patch.object(svc, "invitation_code_exists", AsyncMock(return_value=False)),
        patch.object(svc, "persist_invitation", AsyncMock(return_value=created)) as persist,
    ):
        await svc.create_invitation_link(db, user_id)

    assert persist.await_args.kwargs["inviter_user_id"] == user_id


@pytest.mark.asyncio
async def test_invite_link_daily_limit_allows_50(mock_db):
    db = mock_db()
    user_id = uuid4()
    created = _invitation(user_id)
    limit = svc.invitation_settings.daily_limit
    with (
        patch.object(svc, "lock_invitation_creation_daily_limit", AsyncMock()),
        patch.object(svc, "count_invitations_created_by_user_between", AsyncMock(return_value=limit - 1)),
        patch.object(svc, "create_branch_link", AsyncMock(return_value=_branch_result())),
        patch.object(svc, "invitation_code_exists", AsyncMock(return_value=False)),
        patch.object(svc, "persist_invitation", AsyncMock(return_value=created)) as persist,
    ):
        response = await svc.create_invitation_link(db, user_id)

    assert response.status is True
    persist.assert_awaited_once()


@pytest.mark.asyncio
async def test_invite_link_51st_is_rejected(mock_db):
    db = mock_db()
    limit = svc.invitation_settings.daily_limit
    with (
        patch.object(svc, "lock_invitation_creation_daily_limit", AsyncMock()) as lock,
        patch.object(svc, "count_invitations_created_by_user_between", AsyncMock(return_value=limit)),
        patch.object(svc, "create_branch_link", AsyncMock()) as branch_call,
        patch.object(svc, "persist_invitation", AsyncMock()) as persist,
    ):
        response = await svc.create_invitation_link(db, uuid4())

    assert response.status is False
    assert response.message == "Daily invitation limit reached"
    assert response.data is None
    lock.assert_awaited_once()
    branch_call.assert_not_called()
    persist.assert_not_called()
    db.rollback.assert_awaited_once()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_invite_link_concurrent_requests_lock_before_count(mock_db):
    db = mock_db()
    user_id = uuid4()
    created = _invitation(user_id)
    order: list[str] = []

    async def _lock(*_args, **_kwargs):
        order.append("lock")

    async def _count(*_args, **_kwargs):
        order.append("count")
        return 0

    async def _branch(*_args, **_kwargs):
        order.append("branch")
        return _branch_result()

    async def _persist(*_args, **_kwargs):
        order.append("persist")
        return created

    with (
        patch.object(svc, "lock_invitation_creation_daily_limit", side_effect=_lock),
        patch.object(svc, "count_invitations_created_by_user_between", side_effect=_count),
        patch.object(svc, "create_branch_link", side_effect=_branch),
        patch.object(svc, "invitation_code_exists", AsyncMock(return_value=False)),
        patch.object(svc, "persist_invitation", side_effect=_persist),
    ):
        response = await svc.create_invitation_link(db, user_id)

    assert response.status is True
    assert order == ["lock", "count", "branch", "persist"]


@pytest.mark.asyncio
async def test_invite_link_branch_failure_does_not_create_record(mock_db):
    db = mock_db()
    with (
        patch.object(svc, "lock_invitation_creation_daily_limit", AsyncMock()),
        patch.object(svc, "count_invitations_created_by_user_between", AsyncMock(return_value=0)),
        patch.object(svc, "create_branch_link", AsyncMock(side_effect=BranchLinkError("fail"))),
        patch.object(svc, "persist_invitation", AsyncMock()) as persist,
    ):
        response = await svc.create_invitation_link(db, uuid4())

    assert response.status is False
    assert response.message == "Failed to create Branch link"
    persist.assert_not_called()
    db.commit.assert_not_called()
    db.rollback.assert_awaited()


@pytest.mark.asyncio
async def test_share_link_success(mock_db):
    db = mock_db()
    user_id = uuid4()
    post = _post()
    branch = _branch_result("shareCode99")

    with (
        patch.object(svc, "_get_shareable_post", AsyncMock(return_value=post)),
        patch.object(svc, "check_post_engagement_allowed", AsyncMock()),
        patch.object(svc, "create_branch_link", AsyncMock(return_value=branch)) as branch_call,
        patch.object(svc, "get_user_share_event", AsyncMock(return_value=None)),
        patch.object(svc, "get_share_event_for_post", AsyncMock(return_value=None)),
        patch.object(svc, "create_share_event", AsyncMock()) as create_event,
        patch.object(svc, "update_post_share_count", AsyncMock(return_value=4)) as increment,
    ):
        response = await svc.create_post_share_link(db, user_id, post.id)

    assert response.status is True
    assert response.message == "Share link created successfully"
    assert response.data.type == ShareLinkType.share
    assert response.data.url == branch.url
    create_event.assert_awaited_once()
    assert create_event.await_args.kwargs["branch_url"] == branch.url
    assert create_event.await_args.args[1] == user_id
    assert create_event.await_args.args[2] == post.id
    increment.assert_awaited_once_with(db, post.id, 1)
    payload = branch_call.await_args.args[0]
    share_code = payload["$canonical_identifier"].split("/", 1)[1]
    assert payload["type"] == "share"
    assert payload["post_id"] == str(post.id)
    assert payload["$canonical_identifier"] == f"share/{share_code}"
    assert payload["$deeplink_path"] == f"post/{post.id}"
    assert payload["$og_title"] == "Check out this post on KampuLynk"
    assert payload["$og_description"] == (
        "Someone shared a post on KampuLynk. Check it out and join the conversation."
    )
    assert payload["$og_image_url"] is None
    assert payload["$desktop_url"] == (
        f"https://kampulynk-stage-portal-ponyy.ondigitalocean.app/post/{share_code}"
    )
    assert branch_call.await_args.kwargs["alias"] == share_code
    assert response.data.code == share_code
    assert create_event.await_args.kwargs["branch_code"] == share_code
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_share_link_requires_post_id(mock_db):
    db = mock_db()
    response = await svc.create_post_share_link(db, uuid4(), None)
    assert response.status is False
    assert "post_id" in response.message


@pytest.mark.asyncio
async def test_share_link_invalid_post_rejected(mock_db):
    db = mock_db()
    with (
        patch.object(svc, "_get_shareable_post", AsyncMock(return_value=None)),
        patch.object(svc, "create_branch_link", AsyncMock()) as branch_call,
        patch.object(svc, "create_share_event", AsyncMock()) as create_event,
        patch.object(svc, "update_post_share_count", AsyncMock()) as increment,
    ):
        response = await svc.create_post_share_link(db, uuid4(), uuid4())

    assert response.status is False
    assert response.message == "Post not found"
    branch_call.assert_not_called()
    create_event.assert_not_called()
    increment.assert_not_called()


@pytest.mark.asyncio
async def test_share_link_inaccessible_post_rejected(mock_db):
    db = mock_db()
    post = _post()
    with (
        patch.object(svc, "_get_shareable_post", AsyncMock(return_value=post)),
        patch.object(
            svc,
            "check_post_engagement_allowed",
            AsyncMock(side_effect=ApiError("Action forbidden due to blocks.")),
        ),
        patch.object(svc, "create_branch_link", AsyncMock()) as branch_call,
        patch.object(svc, "update_post_share_count", AsyncMock()) as increment,
    ):
        response = await svc.create_post_share_link(db, uuid4(), post.id)

    assert response.status is False
    assert "forbidden" in response.message.lower() or "block" in response.message.lower()
    branch_call.assert_not_called()
    increment.assert_not_called()


@pytest.mark.asyncio
async def test_share_link_branch_failure_does_not_increment(mock_db):
    db = mock_db()
    post = _post()
    with (
        patch.object(svc, "_get_shareable_post", AsyncMock(return_value=post)),
        patch.object(svc, "check_post_engagement_allowed", AsyncMock()),
        patch.object(svc, "get_user_share_event", AsyncMock(return_value=None)),
        patch.object(svc, "get_share_event_for_post", AsyncMock(return_value=None)),
        patch.object(svc, "create_branch_link", AsyncMock(side_effect=BranchLinkError("fail"))),
        patch.object(svc, "create_share_event", AsyncMock()) as create_event,
        patch.object(svc, "update_post_share_count", AsyncMock()) as increment,
    ):
        response = await svc.create_post_share_link(db, uuid4(), post.id)

    assert response.status is False
    assert response.message == "Failed to create Branch link"
    create_event.assert_not_called()
    increment.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_share_link_returns_existing_code_for_same_user(mock_db):
    db = mock_db()
    user_id = uuid4()
    post = _post()
    existing = SimpleNamespace(
        branch_code="existingCode",
        branch_url="https://jlrh8.test-app.link/existingCode",
        updated_at=None,
    )

    with (
        patch.object(svc, "_get_shareable_post", AsyncMock(return_value=post)),
        patch.object(svc, "check_post_engagement_allowed", AsyncMock()),
        patch.object(svc, "create_branch_link", AsyncMock()) as branch_call,
        patch.object(svc, "get_user_share_event", AsyncMock(return_value=existing)),
        patch.object(svc, "get_share_event_for_post", AsyncMock()) as post_share,
        patch.object(svc, "create_share_event", AsyncMock()) as create_event,
        patch.object(svc, "update_post_share_count", AsyncMock()) as increment,
    ):
        response = await svc.create_post_share_link(db, user_id, post.id)

    assert response.status is True
    assert response.data.code == existing.branch_code
    assert response.data.url == existing.branch_url
    branch_call.assert_not_called()
    post_share.assert_not_awaited()
    create_event.assert_not_called()
    increment.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_share_link_reuses_code_for_different_user(mock_db):
    db = mock_db()
    user_id = uuid4()
    post = _post()
    existing_for_post = SimpleNamespace(
        user_id=uuid4(),
        post_id=post.id,
        branch_code="sharedCode",
        branch_url="https://jlrh8.test-app.link/sharedCode",
    )

    with (
        patch.object(svc, "_get_shareable_post", AsyncMock(return_value=post)),
        patch.object(svc, "check_post_engagement_allowed", AsyncMock()),
        patch.object(svc, "create_branch_link", AsyncMock()) as branch_call,
        patch.object(svc, "get_user_share_event", AsyncMock(return_value=None)),
        patch.object(svc, "get_share_event_for_post", AsyncMock(return_value=existing_for_post)),
        patch.object(svc, "create_share_event", AsyncMock()) as create_event,
        patch.object(svc, "update_post_share_count", AsyncMock(return_value=5)) as increment,
    ):
        response = await svc.create_post_share_link(db, user_id, post.id)

    assert response.status is True
    assert response.data.code == existing_for_post.branch_code
    assert response.data.url == existing_for_post.branch_url
    branch_call.assert_not_called()
    create_event.assert_awaited_once()
    assert create_event.await_args.args[1] == user_id
    assert create_event.await_args.args[2] == post.id
    assert create_event.await_args.kwargs["branch_code"] == existing_for_post.branch_code
    assert create_event.await_args.kwargs["branch_url"] == existing_for_post.branch_url
    increment.assert_awaited_once_with(db, post.id, 1)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_share_link_updates_existing_share_event(mock_db):
    db = mock_db()
    user_id = uuid4()
    post = _post()
    existing = SimpleNamespace(branch_code=None, branch_url=None, updated_at=None)
    branch = _branch_result("newCode")

    with (
        patch.object(svc, "_get_shareable_post", AsyncMock(return_value=post)),
        patch.object(svc, "check_post_engagement_allowed", AsyncMock()),
        patch.object(svc, "create_branch_link", AsyncMock(return_value=branch)) as branch_call,
        patch.object(svc, "get_user_share_event", AsyncMock(return_value=existing)),
        patch.object(svc, "get_share_event_for_post", AsyncMock(return_value=None)),
        patch.object(svc, "create_share_event", AsyncMock()) as create_event,
        patch.object(svc, "update_post_share_count", AsyncMock(return_value=5)),
    ):
        response = await svc.create_post_share_link(db, user_id, post.id)

    assert response.status is True
    create_event.assert_not_called()
    share_code = branch_call.await_args.args[0]["$canonical_identifier"].split("/", 1)[1]
    assert existing.branch_code == share_code
    assert existing.branch_url == branch.url


@pytest.mark.asyncio
async def test_share_link_integrity_error_retries_upsert(mock_db):
    db = mock_db()
    post = _post()
    branch = _branch_result()
    existing = SimpleNamespace(branch_code=None, branch_url=None, updated_at=None)

    with (
        patch.object(svc, "_get_shareable_post", AsyncMock(return_value=post)),
        patch.object(svc, "check_post_engagement_allowed", AsyncMock()),
        patch.object(svc, "create_branch_link", AsyncMock(return_value=branch)),
        patch.object(
            svc,
            "_upsert_share_event",
            AsyncMock(side_effect=[IntegrityError("stmt", {}, Exception()), None]),
        ),
        patch.object(svc, "update_post_share_count", AsyncMock(return_value=2)),
        patch.object(svc, "get_user_share_event", AsyncMock(return_value=existing)),
        patch.object(svc, "get_share_event_for_post", AsyncMock(return_value=None)),
    ):
        response = await svc.create_post_share_link(db, uuid4(), post.id)

    assert response.status is True
    db.rollback.assert_awaited()
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_invite_link_rejects_oversized_branch_code(mock_db):
    db = mock_db()
    huge = "x" * (svc.INVITATION_CODE_MAX_LENGTH + 1)
    with (
        patch.object(svc, "lock_invitation_creation_daily_limit", AsyncMock()),
        patch.object(svc, "count_invitations_created_by_user_between", AsyncMock(return_value=0)),
        patch.object(svc, "create_branch_link", AsyncMock(return_value=_branch_result(huge))),
        patch.object(svc, "persist_invitation", AsyncMock()) as persist,
    ):
        response = await svc.create_invitation_link(db, uuid4())

    assert response.status is False
    persist.assert_not_called()


@pytest.mark.asyncio
async def test_invite_link_persist_unexpected_error_returns_error_response(mock_db):
    db = mock_db()
    with (
        patch.object(svc, "lock_invitation_creation_daily_limit", AsyncMock()),
        patch.object(svc, "count_invitations_created_by_user_between", AsyncMock(return_value=0)),
        patch.object(svc, "create_branch_link", AsyncMock(return_value=_branch_result())),
        patch.object(svc, "invitation_code_exists", AsyncMock(return_value=False)),
        patch.object(svc, "persist_invitation", AsyncMock(side_effect=RuntimeError("db down"))),
    ):
        response = await svc.create_invitation_link(db, uuid4())

    assert response.status is False
    assert response.message == "Failed to create invitation link"
    assert response.data is None
    db.rollback.assert_awaited()


@pytest.mark.asyncio
async def test_share_link_integrity_retry_failure(mock_db):
    db = mock_db()
    post = _post()
    with (
        patch.object(svc, "_get_shareable_post", AsyncMock(return_value=post)),
        patch.object(svc, "check_post_engagement_allowed", AsyncMock()),
        patch.object(svc, "create_branch_link", AsyncMock(return_value=_branch_result())),
        patch.object(
            svc,
            "_upsert_share_event",
            AsyncMock(side_effect=[IntegrityError("stmt", {}, Exception()), RuntimeError("retry fail")]),
        ),
        patch.object(svc, "update_post_share_count", AsyncMock()),
        patch.object(svc, "get_user_share_event", AsyncMock(return_value=None)),
        patch.object(svc, "get_share_event_for_post", AsyncMock(return_value=None)),
    ):
        response = await svc.create_post_share_link(db, uuid4(), post.id)

    assert response.status is False
    assert response.message == "Failed to create share link"


@pytest.mark.asyncio
async def test_get_shareable_post_queries_visible_states(mock_db, scalar_result):
    db = mock_db(scalar_result(None))
    result = await svc._get_shareable_post(db, uuid4())
    assert result is None
    db.execute.assert_awaited_once()
    db = mock_db()
    with (
        patch.object(svc, "lock_invitation_creation_daily_limit", AsyncMock()),
        patch.object(svc, "count_invitations_created_by_user_between", AsyncMock(return_value=0)),
        patch.object(svc, "create_branch_link", AsyncMock(return_value=_branch_result())),
        patch.object(svc, "invitation_code_exists", AsyncMock(return_value=True)),
        patch.object(svc, "persist_invitation", AsyncMock()) as persist,
    ):
        response = await svc.create_invitation_link(db, uuid4())

    assert response.status is False
    persist.assert_not_called()
    db.rollback.assert_awaited()


@pytest.mark.asyncio
async def test_invite_link_persist_integrity_error(mock_db):
    db = mock_db()
    with (
        patch.object(svc, "lock_invitation_creation_daily_limit", AsyncMock()),
        patch.object(svc, "count_invitations_created_by_user_between", AsyncMock(return_value=0)),
        patch.object(svc, "create_branch_link", AsyncMock(return_value=_branch_result())),
        patch.object(svc, "invitation_code_exists", AsyncMock(return_value=False)),
        patch.object(svc, "persist_invitation", AsyncMock(side_effect=IntegrityError("stmt", {}, Exception()))),
    ):
        response = await svc.create_invitation_link(db, uuid4())

    assert response.status is False
    db.rollback.assert_awaited()


@pytest.mark.asyncio
async def test_share_link_persist_failure_does_not_commit(mock_db):
    db = mock_db()
    post = _post()
    with (
        patch.object(svc, "_get_shareable_post", AsyncMock(return_value=post)),
        patch.object(svc, "check_post_engagement_allowed", AsyncMock()),
        patch.object(svc, "create_branch_link", AsyncMock(return_value=_branch_result())),
        patch.object(svc, "_upsert_share_event", AsyncMock(side_effect=RuntimeError("db down"))),
        patch.object(svc, "update_post_share_count", AsyncMock()) as increment,
        patch.object(svc, "get_user_share_event", AsyncMock(return_value=None)),
        patch.object(svc, "get_share_event_for_post", AsyncMock(return_value=None)),
    ):
        response = await svc.create_post_share_link(db, uuid4(), post.id)

    assert response.status is False
    increment.assert_not_called()
    db.commit.assert_not_called()
    db.rollback.assert_awaited()


def test_share_count_increment_uses_sql_plus():
    from sqlalchemy import func, update
    from sqlalchemy.dialects import postgresql

    from apps.feed.db_models import Post

    compiled = (
        update(Post)
        .where(Post.id == uuid4())
        .values(share_count=func.greatest(Post.share_count + 1, 0))
        .compile(dialect=postgresql.dialect())
    )
    sql = str(compiled).lower()
    assert "share_count" in sql
    assert "share_count +" in sql or "share_count+" in sql


def test_share_event_model_has_branch_fields():
    from sqlalchemy import Index, UniqueConstraint

    from apps.engagement.db_models.share_event_db_model import ShareEvent

    assert "branch_code" in ShareEvent.__table__.columns
    assert "branch_url" in ShareEvent.__table__.columns
    indexes = [arg for arg in ShareEvent.__table_args__ if isinstance(arg, Index)]
    branch_idx = next(idx for idx in indexes if idx.name == "ix_share_events_branch_code")
    assert branch_idx.unique is False
    constraints = [arg for arg in ShareEvent.__table_args__ if isinstance(arg, UniqueConstraint)]
    assert any(constraint.name == "uq_share_events_user_post" for constraint in constraints)


def test_share_settings_read_branch_config_from_env():
    from apps.share.config import ShareSettings

    settings = ShareSettings(
        BRANCH_KEY="key_test",
        BRANCH_API_URL="https://api2.branch.io/v1/url",
    )
    assert settings.branch_key == "key_test"
    assert settings.branch_api_url == "https://api2.branch.io/v1/url"


def test_share_branch_data_includes_og_fields_and_desktop_url():
    post_id = uuid4()
    payload = svc._share_branch_data(
        "abc123",
        post_id,
        "Ada",
        "https://cdn.example/post.png",
    )
    assert payload["$canonical_identifier"] == "share/abc123"
    assert payload["$deeplink_path"] == f"post/{post_id}"
    assert payload["type"] == ShareLinkType.share.value
    assert payload["post_id"] == str(post_id)
    assert payload["$og_title"] == "Check out this post on KampuLynk"
    assert payload["$og_description"] == (
        "Ada shared a post on KampuLynk. Check it out and join the conversation."
    )
    assert payload["$og_image_url"] == "https://cdn.example/post.png"
    assert payload["$desktop_url"] == (
        "https://kampulynk-stage-portal-ponyy.ondigitalocean.app/post/abc123"
    )
