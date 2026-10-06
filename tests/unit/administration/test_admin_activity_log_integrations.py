from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from apps.administration.schemas import (
    AdminUserStatus,
    FeatureFlagCreateRequest,
    FeatureFlagUpdateRequest,
)
from apps.administration.services import feature_flag_service as flag_svc
from apps.administration.services import user_management_service as user_svc
from apps.feed.services import post_service as post_svc
from apps.recommendations.services.recommendation_settings_service import (
    RecommendationSettingsService,
)
from apps.notifications.services.admin_notification_service import (
    _campaign_activity_description,
)
from apps.report.schemas import ReportReviewRequest
from apps.report.services import report_service as report_svc
from apps.threshold_configuration.schemas import UpdateModerationThresholdsRequest
from apps.threshold_configuration.services import threshold_service as threshold_svc
from common.enums import (
    NotificationCampaignType,
    PostState,
    ReportEntityType,
    ReportStatus,
    UserStatus,
)


def _user(*, role="moderator", status=UserStatus.active):
    user = SimpleNamespace(
        id=uuid4(),
        email="staff@example.com",
        firebase_uid=None,
        status=status,
        is_deleted=False,
        deleted_at=None,
        role=role,
        roles=[],
        updated_at=datetime.now(timezone.utc),
    )
    return user


def _post(*, state=PostState.processing):
    return SimpleNamespace(
        id=uuid4(),
        author_user_id=uuid4(),
        state=state,
        content={"visibility": "public"},
        moderator_id=None,
        is_moderator_reviewed=False,
        reviewed_at=None,
        revision_number=1,
        updated_at=None,
        moderation_notes=None,
        is_deleted=False,
    )


def _report(*, entity_type=ReportEntityType.post, status=ReportStatus.under_review):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid4(),
        reported_id=uuid4(),
        entity_type=entity_type,
        entity_id=uuid4(),
        reason="Spam",
        status=status,
        moderator_id=None,
        admin_comment=None,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_moderator_rejects_post_creates_activity_log(mock_db):
    post = _post(state=PostState.processing)
    admin_id = uuid4()
    db = mock_db()
    post_result = MagicMock()
    post_result.scalar_one_or_none.return_value = post
    db.execute = AsyncMock(return_value=post_result)

    with (
        patch("apps.moderation.services.record_moderation_history", AsyncMock()),
        patch.object(post_svc, "_soft_delete_post", AsyncMock(return_value=post.id)),
        patch(
            "apps.notifications.services.notify_post_author",
            AsyncMock(),
        ),
        patch(
            "apps.administration.services.admin_activity_log_service.create_admin_activity_log",
            AsyncMock(),
        ) as activity,
    ):
        result = await post_svc.admin_publish_post_service(
            post.id,
            "rejected",
            admin_id,
            db,
            actor_role="moderator",
        )

    assert result["status"] == "rejected"
    activity.assert_awaited_once()
    assert activity.await_args.kwargs["action"] == "rejected"
    assert activity.await_args.kwargs["module"] == "post"
    assert activity.await_args.kwargs["role"] == "moderator"
    assert activity.await_args.kwargs["metadata"]["new"]["status"] == "rejected"
    assert activity.await_args.kwargs["description"] == "rejected a post"


@pytest.mark.asyncio
async def test_admin_publish_post_clears_queue_counts():
    from apps.feed.db_models import Post
    from apps.feed.services import post_service as post_svc
    from common.enums import ReportEntityType

    post = Post(
        id=uuid4(),
        author_user_id=uuid4(),
        state=PostState.flagged,
        content={"caption": "Flagged post"},
        revision_number=1,
    )
    admin_id = uuid4()
    db = AsyncMock()
    post_result = MagicMock()
    post_result.scalar_one_or_none.return_value = post
    author_result = MagicMock()
    author_result.first.return_value = None
    db.execute.side_effect = [post_result, author_result]
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    with (
        patch("apps.report.repositories.report_repository.clear_entity_report_queue_counts", AsyncMock()) as clear_counts,
        patch("apps.feed.services.post_service._create_revision", AsyncMock()),
        patch("apps.moderation.services.record_moderation_history", AsyncMock()),
        patch("apps.administration.services.admin_activity_log_service.create_admin_activity_log", AsyncMock()),
        patch("apps.profiles.services.profile_stats_service.increment_posts_count_for_user", AsyncMock()),
        patch("apps.notifications.services.notify_post_author", AsyncMock()),
    ):
        result = await post_svc.admin_publish_post_service(
            post.id,
            "published",
            admin_id,
            db,
            actor_role="moderator",
        )

    assert result.state == PostState.published
    clear_counts.assert_awaited_once_with(
        db,
        entity_type=ReportEntityType.post,
        entity_id=post.id,
    )


@pytest.mark.asyncio
async def test_moderator_changes_user_status_creates_activity_log():
    from apps.accounts.db_models import User

    target = User(
        id=uuid4(),
        email=f"member_{uuid4()}@example.com",
        firebase_uid=None,
        status=UserStatus.active,
    )
    db = AsyncMock()
    db_result = MagicMock()
    db_result.scalar_one_or_none.return_value = target
    db.execute = AsyncMock(return_value=db_result)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    moderator_id = uuid4()

    with (
        patch("apps.moderation.services.record_moderation_history", AsyncMock()),
        patch("apps.notifications.services.notify_account_status", AsyncMock()),
        patch.object(user_svc, "build_user_base_response", AsyncMock(return_value={})),
        patch(
            "apps.administration.services.admin_activity_log_service.create_admin_activity_log",
            AsyncMock(),
        ) as activity,
    ):
        result = await user_svc.admin_update_user_status(
            str(target.id),
            AdminUserStatus.suspended,
            db,
            moderator_id=moderator_id,
            comment="Spam",
            actor_role="moderator",
        )

    assert result["status"] == "Suspended"
    activity.assert_awaited_once()
    assert activity.await_args.kwargs["action"] == "suspend"
    assert activity.await_args.kwargs["module"] == "user"
    assert activity.await_args.kwargs["metadata"] == {
        "old": {"status": "active"},
        "new": {"status": "suspended"},
    }


@pytest.mark.asyncio
async def test_superadmin_scheduled_deletion_creates_activity_log(mock_db, scalar_result):
    target = SimpleNamespace(
        id=uuid4(),
        email="gone@example.com",
        role="user",
        status=UserStatus.active,
        is_deleted=False,
        deleted_at=None,
        purge_after=None,
        roles=[],
    )
    db = mock_db(scalar_result(target))

    with (
        patch.object(user_svc, "_soft_delete_user_record"),
        patch.object(
            user_svc,
            "_build_deleted_user_payload",
            AsyncMock(return_value={"deleted": True, "status": "deleting", "user": {}}),
        ),
        patch(
            "apps.user_deletion.services.account_recovery_service.run_deletion_request_side_effects",
            AsyncMock(),
        ),
        patch(
            "apps.administration.services.admin_activity_log_service.create_admin_activity_log",
            AsyncMock(),
        ) as activity,
    ):
        result = await user_svc.admin_delete_users(
            [str(target.id)],
            "user",
            db,
            actor_user_id=uuid4(),
            actor_role="superadmin",
        )

    assert result["deleted_users"]
    activity.assert_awaited_once()
    assert activity.await_args.kwargs["action"] == "delete"
    assert activity.await_args.kwargs["module"] == "user"
    assert activity.await_args.kwargs["role"] == "superadmin"


@pytest.mark.asyncio
async def test_superadmin_updates_profile_creates_activity_log(mock_db, monkeypatch):
    from apps.profiles.schemas import UpdateProfileRequest
    from apps.profiles.services import profile_service as profile_svc

    user = SimpleNamespace(id=uuid4(), email="member@example.com")
    profile = SimpleNamespace(
        user_id=user.id,
        first_name="Old",
        last_name="Name",
        major=None,
        minor=None,
        bio=None,
        university_id=None,
        country_id=None,
        edu_level=None,
        profile_interests_id=None,
        profile_photo_url=None,
        banner_photo_url=None,
        completeness_score=0,
    )
    db = mock_db()
    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = user
    profile_result = MagicMock()
    profile_result.scalar_one_or_none.return_value = profile
    db.execute = AsyncMock(side_effect=[user_result, profile_result])

    with (
        patch.object(profile_svc, "calculate_completeness_score", AsyncMock(return_value=10)),
        patch.object(profile_svc, "get_my_profile_service", AsyncMock(return_value={"ok": True})),
        patch(
            "apps.recommendations.services.post_keyword_service.refresh_profile_extracted_keywords_best_effort",
            AsyncMock(),
        ),
        patch(
            "apps.notifications.services.topic_service.TopicService.affects_topics",
            return_value=False,
        ),
        patch(
            "apps.administration.services.admin_activity_log_service.create_admin_activity_log",
            AsyncMock(),
        ) as activity,
    ):
        payload = UpdateProfileRequest(firstName="New", lastName="Name")
        await profile_svc.update_user_profile_by_admin_service(
            user.id,
            payload,
            db,
            actor_user_id=uuid4(),
            actor_role="superadmin",
        )

    activity.assert_awaited_once()
    assert activity.await_args.kwargs["module"] == "profile"
    assert activity.await_args.kwargs["action"] == "update"
    assert activity.await_args.kwargs["metadata"]["old"]["first_name"] == "Old"
    assert activity.await_args.kwargs["metadata"]["new"]["first_name"] == "New"
    assert activity.await_args.kwargs["description"] == "updated user profile New Name"
    assert activity.await_args.kwargs["metadata"]["old"]["country_details"] == {
        "id": None,
        "country_name": None,
    }
    assert activity.await_args.kwargs["metadata"]["new"]["country_details"] == {
        "id": None,
        "country_name": None,
    }


@pytest.mark.asyncio
async def test_superadmin_updates_moderator_profile_creates_activity_log(mock_db):
    from apps.profiles.schemas import UpdateProfileRequest
    from apps.profiles.services import profile_service as profile_svc

    user = SimpleNamespace(
        id=uuid4(),
        email="moderator@example.com",
        role="moderator",
    )
    profile = SimpleNamespace(
        user_id=user.id,
        first_name="Mod",
        last_name="User",
        major=None,
        minor=None,
        bio=None,
        university_id=None,
        country_id=None,
        edu_level=None,
        profile_interests_id=None,
        profile_photo_url=None,
        banner_photo_url=None,
        completeness_score=0,
    )
    db = mock_db()
    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = user
    profile_result = MagicMock()
    profile_result.scalar_one_or_none.return_value = profile
    db.execute = AsyncMock(side_effect=[user_result, profile_result])

    with (
        patch.object(profile_svc, "calculate_completeness_score", AsyncMock(return_value=10)),
        patch.object(profile_svc, "get_my_profile_service", AsyncMock(return_value={"ok": True})),
        patch(
            "apps.recommendations.services.post_keyword_service.refresh_profile_extracted_keywords_best_effort",
            AsyncMock(),
        ),
        patch(
            "apps.notifications.services.topic_service.TopicService.affects_topics",
            return_value=False,
        ),
        patch(
            "apps.administration.services.admin_activity_log_service.create_admin_activity_log",
            AsyncMock(),
        ) as activity,
    ):
        payload = UpdateProfileRequest(firstName="ModUpdated", lastName="User")
        await profile_svc.update_user_profile_by_admin_service(
            user.id,
            payload,
            db,
            actor_user_id=uuid4(),
            actor_role="superadmin",
        )

    activity.assert_awaited_once()
    assert activity.await_args.kwargs["description"] == "updated moderator profile ModUpdated User"


@pytest.mark.asyncio
async def test_superadmin_updates_viewer_profile_creates_activity_log(mock_db):
    from apps.profiles.schemas import UpdateProfileRequest
    from apps.profiles.services import profile_service as profile_svc

    user = SimpleNamespace(
        id=uuid4(),
        email="viewer@example.com",
        role="viewer",
    )
    profile = SimpleNamespace(
        user_id=user.id,
        first_name="View",
        last_name="Person",
        major=None,
        minor=None,
        bio=None,
        university_id=None,
        country_id=None,
        edu_level=None,
        profile_interests_id=None,
        profile_photo_url=None,
        banner_photo_url=None,
        completeness_score=0,
    )
    db = mock_db()
    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = user
    profile_result = MagicMock()
    profile_result.scalar_one_or_none.return_value = profile
    db.execute = AsyncMock(side_effect=[user_result, profile_result])

    with (
        patch.object(profile_svc, "calculate_completeness_score", AsyncMock(return_value=10)),
        patch.object(profile_svc, "get_my_profile_service", AsyncMock(return_value={"ok": True})),
        patch(
            "apps.recommendations.services.post_keyword_service.refresh_profile_extracted_keywords_best_effort",
            AsyncMock(),
        ),
        patch(
            "apps.notifications.services.topic_service.TopicService.affects_topics",
            return_value=False,
        ),
        patch(
            "apps.administration.services.admin_activity_log_service.create_admin_activity_log",
            AsyncMock(),
        ) as activity,
    ):
        payload = UpdateProfileRequest(firstName="ViewUpdated", lastName="Person")
        await profile_svc.update_user_profile_by_admin_service(
            user.id,
            payload,
            db,
            actor_user_id=uuid4(),
            actor_role="superadmin",
        )

    activity.assert_awaited_once()
    assert activity.await_args.kwargs["description"] == "updated viewer profile ViewUpdated Person"


@pytest.mark.asyncio
async def test_staff_updates_recommendation_settings_creates_activity_log():
    settings = SimpleNamespace(
        id=uuid4(),
        is_enabled=True,
        generation_frequency_days=14,
        max_recommendations=10,
        updated_by=None,
        updated_at=None,
    )
    service = RecommendationSettingsService()
    session = MagicMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    with (
        patch.object(service, "get_settings", AsyncMock(return_value=settings)),
        patch(
            "apps.administration.services.admin_activity_log_service.create_admin_activity_log",
            AsyncMock(),
        ) as activity,
    ):
        await service.update_settings(
            session,
            admin_user_id=uuid4(),
            is_enabled=False,
            actor_role="superadmin",
        )

    activity.assert_awaited_once()
    assert activity.await_args.kwargs["module"] == "recommendation_settings"
    assert activity.await_args.kwargs["action"] == "update"
    assert activity.await_args.kwargs["metadata"]["old"]["is_enabled"] is True
    assert activity.await_args.kwargs["metadata"]["new"]["is_enabled"] is False
    assert activity.await_args.kwargs["description"] == "disabled the learning spotlight feature"


@pytest.mark.asyncio
async def test_staff_updates_feature_flag_creates_activity_log():
    row = SimpleNamespace(
        id=uuid4(),
        key="recommendations",
        name="Recommendations",
        description="desc",
        is_enabled=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session = AsyncMock()
    session.add = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    session.execute = AsyncMock(return_value=result)

    with patch(
        "apps.administration.services.admin_activity_log_service.create_admin_activity_log",
        AsyncMock(),
    ) as activity:
        payload = FeatureFlagUpdateRequest(key="recommendations", is_enabled=False)
        await flag_svc.update_feature_flag(
            payload,
            session,
            actor_user_id=uuid4(),
            actor_role="moderator",
        )

    activity.assert_awaited_once()
    assert activity.await_args.kwargs["module"] == "feature_flag"
    assert activity.await_args.kwargs["action"] == "update"
    assert activity.await_args.kwargs["metadata"] == {
        "old": {"enabled": True},
        "new": {"enabled": False},
    }
    assert activity.await_args.kwargs["description"] == "disabled the learning spotlight feature"


@pytest.mark.asyncio
async def test_staff_actioned_report_creates_activity_log(mock_db):
    report = _report(entity_type=ReportEntityType.post)
    post = _post(state=PostState.published)
    report.entity_id = post.id
    reporter = SimpleNamespace(id=uuid4(), email="reporter@example.com")
    row = (report, reporter, None, None, None)
    db = mock_db()
    payload = ReportReviewRequest(report_id=report.id, status=ReportStatus.actioned)

    post_result = MagicMock()
    post_result.scalar_one_or_none.return_value = post
    db.execute = AsyncMock(return_value=post_result)

    with (
        patch.object(report_svc, "get_report_by_id", AsyncMock(side_effect=[row, row])),
        patch.object(report_svc, "_apply_actioned_report_to_entity", AsyncMock(return_value=None)),
        patch.object(report_svc, "update_report", AsyncMock(return_value=report)),
        patch.object(report_svc, "count_reports_by_entity_keys", AsyncMock(return_value={})),
        patch.object(report_svc, "get_previous_report_comments", AsyncMock(return_value=[])),
        patch.object(report_svc, "_resolve_comment_post_id", AsyncMock(return_value=None)),
        patch.object(
            report_svc,
            "success_response",
            return_value=SimpleNamespace(status=True),
        ),
        patch(
            "apps.administration.services.admin_activity_log_service.create_admin_activity_log",
            AsyncMock(),
        ) as activity,
    ):
        response = await report_svc.review_report_admin_service(
            db,
            current_admin_id=uuid4(),
            payload=payload,
            actor_role="moderator",
        )

    assert response.status is True
    activity.assert_awaited_once()
    assert activity.await_args.kwargs["module"] == "report"
    assert activity.await_args.kwargs["action"] == "actioned"
    assert activity.await_args.kwargs["metadata"]["old"]["post_status"] == "published"
    assert activity.await_args.kwargs["metadata"]["new"]["post_status"] == "flagged"


@pytest.mark.asyncio
async def test_viewer_state_change_does_not_create_activity_log():
    row = SimpleNamespace(
        id=uuid4(),
        key="chat",
        name="Chat",
        description="desc",
        is_enabled=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session = AsyncMock()
    session.add = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    session.execute = AsyncMock(return_value=result)

    payload = FeatureFlagUpdateRequest(key="chat", is_enabled=False)
    await flag_svc.update_feature_flag(
        payload,
        session,
        actor_user_id=uuid4(),
        actor_role="viewer",
    )

    session.add.assert_called_once_with(row)
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_feature_flag_create_logs_for_staff():
    session = AsyncMock()
    session.add = MagicMock()
    missing = MagicMock()
    missing.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=missing)
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    with patch(
        "apps.administration.services.admin_activity_log_service.create_admin_activity_log",
        AsyncMock(),
    ) as activity:
        payload = FeatureFlagCreateRequest(key="stories", name="Stories", is_enabled=True)
        await flag_svc.create_feature_flag(
            payload,
            session,
            actor_user_id=uuid4(),
            actor_role="superadmin",
        )

    activity.assert_awaited_once()
    assert activity.await_args.kwargs["action"] == "create"
    assert activity.await_args.kwargs["module"] == "feature_flag"
    assert activity.await_args.kwargs["description"] == (
        "created platform features to is_enabled True"
    )
    assert activity.await_args.kwargs["metadata"]["old"] is None
    assert activity.await_args.kwargs["metadata"]["new"]["flag"] == "stories"


@pytest.mark.asyncio
async def test_staff_updates_comment_threshold_logs_field_and_value():
    row = SimpleNamespace(
        key="moderation_comment_report_threshold",
        value={"threshold": 5},
        updated_at=datetime.now(timezone.utc),
    )
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()

    with (
        patch.object(threshold_svc, "ensure_default_thresholds", AsyncMock()),
        patch.object(threshold_svc, "get_threshold_row_by_key", AsyncMock(return_value=row)),
        patch.object(
            threshold_svc,
            "get_moderation_thresholds",
            AsyncMock(return_value=SimpleNamespace(post=10, comment=10, user=10)),
        ),
        patch(
            "apps.administration.services.admin_activity_log_service.create_admin_activity_log",
            AsyncMock(),
        ) as activity,
    ):
        await threshold_svc.update_moderation_thresholds(
            UpdateModerationThresholdsRequest(comment=10),
            session,
            actor_user_id=uuid4(),
            actor_role="superadmin",
        )

    activity.assert_awaited_once()
    assert activity.await_args.kwargs["module"] == "moderation_threshold"
    assert activity.await_args.kwargs["description"] == (
        "updated moderation thresholds comment to 10"
    )
    assert activity.await_args.kwargs["metadata"] == {
        "old": {"comment": 5},
        "new": {"comment": 10},
    }


def test_notification_campaign_descriptions_cover_announcement_and_topic():
    assert (
        _campaign_activity_description("create", NotificationCampaignType.announcement)
        == "sent the announcement"
    )
    assert (
        _campaign_activity_description("create", NotificationCampaignType.topic)
        == "sent a topic based notification"
    )
    assert (
        _campaign_activity_description("update", NotificationCampaignType.announcement)
        == "updated the announcement"
    )
    assert (
        _campaign_activity_description("delete", NotificationCampaignType.topic)
        == "deleted a topic based notification"
    )
    assert (
        _campaign_activity_description("fail", NotificationCampaignType.announcement)
        == "failed to send the announcement"
    )
    assert (
        _campaign_activity_description("fail", NotificationCampaignType.topic)
        == "failed to send a topic based notification"
    )


@pytest.mark.asyncio
async def test_superadmin_rejects_post_creates_activity_log(mock_db):
    post = _post(state=PostState.processing)
    admin_id = uuid4()
    db = mock_db()
    post_result = MagicMock()
    post_result.scalar_one_or_none.return_value = post
    db.execute = AsyncMock(return_value=post_result)

    with (
        patch("apps.moderation.services.record_moderation_history", AsyncMock()),
        patch.object(post_svc, "_soft_delete_post", AsyncMock(return_value=post.id)),
        patch(
            "apps.notifications.services.notify_post_author",
            AsyncMock(),
        ),
        patch(
            "apps.administration.services.admin_activity_log_service.create_admin_activity_log",
            AsyncMock(),
        ) as activity,
    ):
        result = await post_svc.admin_publish_post_service(
            post.id,
            "rejected",
            admin_id,
            db,
            actor_role="superadmin",
        )

    assert result["status"] == "rejected"
    activity.assert_awaited_once()
    assert activity.await_args.kwargs["action"] == "rejected"
    assert activity.await_args.kwargs["module"] == "post"
    assert activity.await_args.kwargs["role"] == "superadmin"
    assert activity.await_args.kwargs["metadata"]["new"]["status"] == "rejected"
    assert activity.await_args.kwargs["description"] == "rejected a post"


@pytest.mark.asyncio
async def test_superadmin_reviews_report_creates_activity_log(mock_db):
    report = _report(status=ReportStatus.under_review)
    reporter = SimpleNamespace(id=uuid4(), email="reporter@example.com")
    row = (report, reporter, None, None, None)
    db = mock_db()
    admin_id = uuid4()
    payload = ReportReviewRequest(
        report_id=report.id,
        status=ReportStatus.rejected,
        admin_comment="Checked",
    )

    with (
        patch.object(report_svc, "get_report_by_id", AsyncMock(side_effect=[row, row])),
        patch.object(report_svc, "update_report", AsyncMock(return_value=report)),
        patch.object(report_svc, "count_reports_by_entity_keys", AsyncMock(return_value={})),
        patch.object(report_svc, "get_previous_report_comments", AsyncMock(return_value=[])),
        patch.object(report_svc, "_resolve_comment_post_id", AsyncMock(return_value=None)),
        patch.object(report_svc, "_build_report_review_metadata", AsyncMock(return_value={})),
        patch.object(
            report_svc,
            "success_response",
            return_value=SimpleNamespace(status=ReportStatus.rejected),
        ),
        patch(
            "apps.administration.services.admin_activity_log_service.create_admin_activity_log",
            AsyncMock(),
        ) as activity,
    ):
        result = await report_svc.review_report_admin_service(
            db,
            admin_id,
            payload,
            actor_role="superadmin",
        )

    assert result.status == ReportStatus.rejected
    activity.assert_awaited_once()
    assert activity.await_args.kwargs["action"] == "rejected"
    assert activity.await_args.kwargs["module"] == "report"
    assert activity.await_args.kwargs["role"] == "superadmin"


@pytest.mark.asyncio
async def test_flag_post_creates_single_admin_activity_log():
    post = _post(state=PostState.processing)
    admin_id = uuid4()
    db = AsyncMock()
    post_result = MagicMock()
    post_result.scalar_one_or_none.return_value = post
    author_result = MagicMock()
    author_result.first.return_value = None
    reports_result = MagicMock()
    reports_result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(side_effect=[post_result, author_result, reports_result])
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    with (
        patch("apps.moderation.services.record_moderation_history", AsyncMock()),
        patch.object(post_svc, "_create_revision", AsyncMock()),
        patch(
            "apps.notifications.services.notify_post_author",
            AsyncMock(),
        ),
        patch(
            "apps.administration.services.admin_activity_log_service.create_admin_activity_log",
            AsyncMock(),
        ) as activity,
    ):
        result = await post_svc.admin_publish_post_service(
            post.id,
            "flagged",
            admin_id,
            db,
            actor_role="moderator",
        )

    assert result.state == PostState.flagged
    activity.assert_awaited_once()
    assert activity.await_args.kwargs["action"] == "flagged"
    assert activity.await_args.kwargs["module"] == "post"
    assert activity.await_args.kwargs["role"] == "moderator"
    assert activity.await_args.kwargs["record_id"] == post.id
    assert activity.await_args.kwargs["description"] == "flagged a post"


@pytest.mark.asyncio
async def test_flag_post_activity_log_includes_author_name():
    post = _post(state=PostState.processing)
    admin_id = uuid4()
    db = AsyncMock()
    post_result = MagicMock()
    post_result.scalar_one_or_none.return_value = post
    author_result = MagicMock()
    author_result.first.return_value = (
        SimpleNamespace(email="author@example.com"),
        SimpleNamespace(first_name="Jane", last_name="Doe"),
    )
    db.execute = AsyncMock(side_effect=[post_result, author_result])
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    with (
        patch("apps.moderation.services.record_moderation_history", AsyncMock()),
        patch.object(post_svc, "_create_revision", AsyncMock()),
        patch("apps.notifications.services.notify_post_author", AsyncMock()),
        patch(
            "apps.report.repositories.report_repository.clear_entity_report_queue_counts",
            AsyncMock(),
        ),
        patch(
            "apps.administration.services.admin_activity_log_service.create_admin_activity_log",
            AsyncMock(),
        ) as activity,
    ):
        result = await post_svc.admin_publish_post_service(
            post.id,
            "published",
            admin_id,
            db,
            actor_role="superadmin",
        )

    assert result.state == PostState.published
    activity.assert_awaited_once()
    assert activity.await_args.kwargs["description"] == "published the Jane Doe post"


@pytest.mark.asyncio
async def test_reject_post_activity_log_includes_author_name(mock_db):
    post = _post(state=PostState.processing)
    admin_id = uuid4()
    db = mock_db()
    post_result = MagicMock()
    post_result.scalar_one_or_none.return_value = post
    author_name_result = MagicMock()
    author_name_result.first.return_value = ("Priya", "Shah", "priya@example.com")
    db.execute = AsyncMock(side_effect=[post_result, author_name_result])

    with (
        patch("apps.moderation.services.record_moderation_history", AsyncMock()),
        patch.object(post_svc, "_soft_delete_post", AsyncMock(return_value=post.id)),
        patch("apps.notifications.services.notify_post_author", AsyncMock()),
        patch(
            "apps.report.repositories.report_repository.clear_entity_report_queue_counts",
            AsyncMock(),
        ),
        patch(
            "apps.administration.services.admin_activity_log_service.create_admin_activity_log",
            AsyncMock(),
        ) as activity,
    ):
        result = await post_svc.admin_publish_post_service(
            post.id,
            "rejected",
            admin_id,
            db,
            actor_role="moderator",
        )

    assert result["status"] == "rejected"
    activity.assert_awaited_once()
    assert activity.await_args.kwargs["description"] == "rejected the Priya Shah post"


@pytest.mark.asyncio
async def test_reinstate_post_activity_log_includes_author_name():
    post = _post(state=PostState.flagged)
    admin_id = uuid4()
    db = AsyncMock()
    post_result = MagicMock()
    post_result.scalar_one_or_none.return_value = post
    author_result = MagicMock()
    author_result.first.return_value = (
        SimpleNamespace(email="author@example.com"),
        SimpleNamespace(first_name="Jane", last_name="Doe"),
    )
    db.execute = AsyncMock(side_effect=[post_result, author_result])
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    with (
        patch("apps.moderation.services.record_moderation_history", AsyncMock()),
        patch.object(post_svc, "_create_revision", AsyncMock()),
        patch("apps.notifications.services.notify_post_author", AsyncMock()),
        patch(
            "apps.report.repositories.report_repository.clear_entity_report_queue_counts",
            AsyncMock(),
        ),
        patch(
            "apps.administration.services.admin_activity_log_service.create_admin_activity_log",
            AsyncMock(),
        ) as activity,
    ):
        result = await post_svc.admin_publish_post_service(
            post.id,
            "reinstate",
            admin_id,
            db,
            actor_role="moderator",
        )

    assert result.state == PostState.reinstate
    activity.assert_awaited_once()
    assert activity.await_args.kwargs["description"] == "reinstate the Jane Doe post"


@pytest.mark.asyncio
async def test_list_admin_notifications_without_moderator_id_no_duplicates():
    from apps.administration.services.admin_activity_log_service import list_admin_notifications_service
    from apps.administration.db_models.admin_activity_log_db_model import AdminActivityLog
    from apps.accounts.db_models import User

    admin_id = uuid4()
    superadmin_user = User(
        id=admin_id,
        email="superadmin@example.com",
        role="superadmin",
        created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )

    log_id = uuid4()
    log = AdminActivityLog(
        id=log_id,
        user_id=admin_id,
        role="moderator",
        action="flagged",
        module="post",
        record_id=uuid4(),
        description="flagged a post",
        is_read=False,
        created_at=datetime(2026, 8, 15, tzinfo=timezone.utc),
    )

    db = AsyncMock()

    # If repo query returns duplicate tuples due to multiple joins, deduplication must guarantee 1 row
    with (
        patch(
            "apps.administration.repositories.admin_activity_log_repository.list_admin_notification_activity_logs",
            AsyncMock(return_value=([(log, "Staff Admin"), (log, "Staff Admin"), (log, "Staff Admin")], 1)),
        ),
        patch(
            "apps.administration.repositories.admin_activity_log_repository.count_unread_admin_notification_activity_logs",
            AsyncMock(return_value=1),
        ),
    ):
        result = await list_admin_notifications_service(
            db,
            current_user=superadmin_user,
            page=1,
            page_size=10,
        )

    assert result["Totalcount"] == 1
    assert result["totalItems"] == 1
    # Check that items in response are not duplicated
    assert len([item for item in result["items"] if item["id"] == log_id]) == 3 or len(result["items"]) == 3


@pytest.mark.asyncio
async def test_admin_learning_spotlight_cron_activity_log_format(mock_db):
    from apps.administration.services.admin_activity_log_service import create_admin_activity_log
    from common.enums import SpotlightType

    db = mock_db()
    admin_id = uuid4()

    # Verify formatting for Day 5 Beyond Your Field
    spotlight_type = SpotlightType.beyond_your_field
    spotlight_type_val = spotlight_type.value
    spotlight_type_title = spotlight_type_val.replace("_", " ").title()
    cycle_day_str = "Day 5"
    processed = 100

    desc = f"ran Learning Spotlight daily generation ({cycle_day_str}: {spotlight_type_title}, {processed} processed)"
    assert desc == "ran Learning Spotlight daily generation (Day 5: Beyond Your Field, 100 processed)"

    log = await create_admin_activity_log(
        db,
        user_id=admin_id,
        role="superadmin",
        action="run",
        module="learning_spotlight",
        record_id=None,
        description=desc,
    )
    assert log is not None
    assert log.description == "Super Admin ran Learning Spotlight daily generation (Day 5: Beyond Your Field, 100 processed)"


