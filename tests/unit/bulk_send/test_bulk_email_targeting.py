from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from apps.bulk_send.enums import BulkEmailTargetType
from apps.bulk_send.recipient_resolution import (
    bulk_email_preference_opt_out_message,
    resolve_bulk_email_audience,
    resolve_bulk_email_user_ids,
)
from apps.bulk_send.schemas import BulkEmailTarget, CreateBulkCampaignRequest
from common.enums import NotificationTargetType


def test_bulk_email_target_to_all_true_requires_empty_values():
    with pytest.raises(ValidationError) as exc:
        BulkEmailTarget(type=BulkEmailTargetType.MAJOR, to_all=True, values=["1"])
    assert "values must be empty when to_all is true" in str(exc.value)


def test_bulk_email_target_to_all_false_requires_values():
    with pytest.raises(ValidationError) as exc:
        BulkEmailTarget(type=BulkEmailTargetType.MAJOR, to_all=False, values=[])
    assert "values must contain at least one" in str(exc.value)


def test_bulk_email_target_user_to_all_forbidden():
    with pytest.raises(ValidationError) as exc:
        BulkEmailTarget(type=BulkEmailTargetType.USER, to_all=True, values=[])
    assert "USER targets cannot use to_all=true" in str(exc.value)


def test_bulk_email_target_invalid_type():
    with pytest.raises(ValidationError):
        BulkEmailTarget.model_validate(
            {"type": "INVALID", "to_all": False, "values": ["x"]}
        )


def test_bulk_email_target_uuid_types_validate():
    with pytest.raises(ValidationError) as exc:
        BulkEmailTarget(
            type=BulkEmailTargetType.UNIVERSITY,
            to_all=False,
            values=["not-a-uuid"],
        )
    assert "must be valid UUIDs" in str(exc.value)

    ok = BulkEmailTarget(
        type=BulkEmailTargetType.USER,
        to_all=False,
        values=[str(uuid4())],
    )
    assert len(ok.values) == 1


def test_bulk_email_target_hashtag_allows_strings():
    target = BulkEmailTarget(
        type=BulkEmailTargetType.HASHTAG,
        to_all=False,
        values=["ai", "#machine-learning"],
    )
    assert target.values == ["ai", "#machine-learning"]


def test_bulk_email_target_major_allows_numeric_ids():
    target = BulkEmailTarget(
        type=BulkEmailTargetType.MAJOR,
        to_all=False,
        values=["1", "Computer Science"],
    )
    assert target.values == ["1", "Computer Science"]


def test_bulk_email_target_minor_allows_numeric_ids_and_names():
    target = BulkEmailTarget(
        type=BulkEmailTargetType.MINOR,
        to_all=False,
        values=["10", "Data Science"],
    )
    assert target.type == BulkEmailTargetType.MINOR
    assert target.values == ["10", "Data Science"]


def test_create_request_accepts_minor_target():
    payload = CreateBulkCampaignRequest(
        name="Minor Campaign",
        subject="Hi",
        body_html="<p>x</p>",
        targets=[
            BulkEmailTarget(
                type=BulkEmailTargetType.MINOR,
                to_all=False,
                values=["5"],
            )
        ],
    )
    assert payload.targets[0].type == BulkEmailTargetType.MINOR
    assert payload.targets[0].values == ["5"]


def test_bulk_email_target_education_level_allows_names_and_ids():
    target = BulkEmailTarget(
        type=BulkEmailTargetType.EDUCATION_LEVEL,
        to_all=False,
        values=["Bachelors", "1", "Masters"],
    )
    assert target.type == BulkEmailTargetType.EDUCATION_LEVEL
    assert target.values == ["Bachelors", "1", "Masters"]


def test_create_request_accepts_education_level_target():
    payload = CreateBulkCampaignRequest(
        name="Edu Campaign",
        subject="Hi",
        body_html="<p>x</p>",
        targets=[
            BulkEmailTarget(
                type=BulkEmailTargetType.EDUCATION_LEVEL,
                to_all=False,
                values=["Bachelors"],
            )
        ],
    )
    assert payload.targets[0].type == BulkEmailTargetType.EDUCATION_LEVEL
    assert payload.targets[0].values == ["Bachelors"]


def test_create_request_normalizes_legacy_country_and_user():
    user_id = uuid4()
    country_id = uuid4()
    payload = CreateBulkCampaignRequest(
        name="Legacy",
        subject="Hi",
        body_html="<p>x</p>",
        user_ids=[user_id],
        country_ids=[country_id],
    )
    assert [t.type for t in payload.targets] == [
        BulkEmailTargetType.USER,
        BulkEmailTargetType.COUNTRY,
    ]
    assert payload.targets[0].values == [str(user_id)]
    assert payload.targets[1].values == [str(country_id)]


def test_create_request_prefers_explicit_targets_over_legacy():
    user_id = uuid4()
    payload = CreateBulkCampaignRequest(
        name="Mixed",
        subject="Hi",
        body_html="<p>x</p>",
        user_ids=[user_id],
        targets=[
            BulkEmailTarget(
                type=BulkEmailTargetType.MAJOR,
                to_all=False,
                values=["10"],
            )
        ],
    )
    assert len(payload.targets) == 1
    assert payload.targets[0].type == BulkEmailTargetType.MAJOR


@pytest.mark.asyncio
async def test_resolve_skips_to_all_targets(monkeypatch):
    u1 = uuid4()
    captured = {}

    async def _fake_resolve_topic(db, *, targets):
        captured["targets"] = targets
        return [u1]

    async def _fake_filter(db, user_ids, *, is_alumni=False):
        return user_ids

    async def _fake_pref_filter(db, user_ids, *, preference):
        return user_ids

    monkeypatch.setattr(
        "apps.bulk_send.recipient_resolution.resolve_topic_recipients",
        _fake_resolve_topic,
    )
    monkeypatch.setattr(
        "apps.bulk_send.recipient_resolution._filter_email_eligible_user_ids",
        _fake_filter,
    )
    monkeypatch.setattr(
        "apps.notifications.repositories.notification_repository.filter_users_eligible_for_email_preference",
        _fake_pref_filter,
    )

    result = await resolve_bulk_email_user_ids(
        db=object(),  # type: ignore[arg-type]
        targets=[
            BulkEmailTarget(type=BulkEmailTargetType.MAJOR, to_all=True, values=[]),
            BulkEmailTarget(
                type=BulkEmailTargetType.UNIVERSITY,
                to_all=False,
                values=[str(uuid4())],
            ),
        ],
        is_alumni=False,
    )

    assert result == [u1]
    assert len(captured["targets"]) == 1
    assert captured["targets"][0][0] == NotificationTargetType.university


@pytest.mark.asyncio
async def test_resolve_maps_minor_target(monkeypatch):
    u1 = uuid4()
    captured = {}

    async def _fake_resolve_topic(db, *, targets):
        captured["targets"] = targets
        return [u1]

    async def _fake_filter(db, user_ids, *, is_alumni=False):
        return user_ids

    async def _fake_pref_filter(db, user_ids, *, preference):
        return user_ids

    monkeypatch.setattr(
        "apps.bulk_send.recipient_resolution.resolve_topic_recipients",
        _fake_resolve_topic,
    )
    monkeypatch.setattr(
        "apps.bulk_send.recipient_resolution._filter_email_eligible_user_ids",
        _fake_filter,
    )
    monkeypatch.setattr(
        "apps.notifications.repositories.notification_repository.filter_users_eligible_for_email_preference",
        _fake_pref_filter,
    )

    result = await resolve_bulk_email_user_ids(
        db=object(),  # type: ignore[arg-type]
        targets=[
            BulkEmailTarget(
                type=BulkEmailTargetType.MINOR,
                to_all=False,
                values=["10", "Data Science"],
            ),
        ],
        is_alumni=False,
    )

    assert result == [u1]
    assert captured["targets"][0][0] == NotificationTargetType.minor
    assert captured["targets"][0][1] == ["10", "Data Science"]


@pytest.mark.asyncio
async def test_resolve_maps_education_level_target(monkeypatch):
    u1 = uuid4()
    captured = {}

    async def _fake_resolve_topic(db, *, targets):
        captured["targets"] = targets
        return [u1]

    async def _fake_filter(db, user_ids, *, is_alumni=False):
        return user_ids

    async def _fake_pref_filter(db, user_ids, *, preference):
        return user_ids

    monkeypatch.setattr(
        "apps.bulk_send.recipient_resolution.resolve_topic_recipients",
        _fake_resolve_topic,
    )
    monkeypatch.setattr(
        "apps.bulk_send.recipient_resolution._filter_email_eligible_user_ids",
        _fake_filter,
    )
    monkeypatch.setattr(
        "apps.notifications.repositories.notification_repository.filter_users_eligible_for_email_preference",
        _fake_pref_filter,
    )

    result = await resolve_bulk_email_user_ids(
        db=object(),  # type: ignore[arg-type]
        targets=[
            BulkEmailTarget(
                type=BulkEmailTargetType.EDUCATION_LEVEL,
                to_all=False,
                values=["Bachelors", "Masters"],
            ),
        ],
        is_alumni=False,
    )

    assert result == [u1]
    assert captured["targets"][0][0] == NotificationTargetType.education_level
    assert captured["targets"][0][1] == ["Bachelors", "Masters"]


@pytest.mark.asyncio
async def test_resolve_empty_targets_uses_all_eligible(monkeypatch):
    u1, u2 = uuid4(), uuid4()

    async def _list_all(db, *, is_alumni=False):
        assert is_alumni is True
        return [u1, u2]

    async def _fake_pref_filter(db, user_ids, *, preference):
        return user_ids

    monkeypatch.setattr(
        "apps.bulk_send.recipient_resolution._list_email_eligible_user_ids",
        _list_all,
    )
    monkeypatch.setattr(
        "apps.notifications.repositories.notification_repository.filter_users_eligible_for_email_preference",
        _fake_pref_filter,
    )

    result = await resolve_bulk_email_user_ids(
        db=object(),  # type: ignore[arg-type]
        targets=[],
        is_alumni=True,
    )
    assert result == [u1, u2]


@pytest.mark.asyncio
async def test_resolve_all_to_all_targets_uses_all_eligible(monkeypatch):
    u1 = uuid4()

    async def _list_all(db, *, is_alumni=False):
        return [u1]

    async def _fake_pref_filter(db, user_ids, *, preference):
        return user_ids

    monkeypatch.setattr(
        "apps.bulk_send.recipient_resolution._list_email_eligible_user_ids",
        _list_all,
    )
    monkeypatch.setattr(
        "apps.notifications.repositories.notification_repository.filter_users_eligible_for_email_preference",
        _fake_pref_filter,
    )

    result = await resolve_bulk_email_user_ids(
        db=object(),  # type: ignore[arg-type]
        targets=[
            BulkEmailTarget(type=BulkEmailTargetType.MAJOR, to_all=True, values=[]),
            BulkEmailTarget(type=BulkEmailTargetType.COUNTRY, to_all=True, values=[]),
        ],
        is_alumni=False,
    )
    assert result == [u1]


@pytest.mark.asyncio
async def test_resolve_maps_interest_hashtag_user(monkeypatch):
    captured = {}

    async def _fake_resolve_topic(db, *, targets):
        captured["targets"] = targets
        return []

    monkeypatch.setattr(
        "apps.bulk_send.recipient_resolution.resolve_topic_recipients",
        _fake_resolve_topic,
    )

    await resolve_bulk_email_user_ids(
        db=object(),  # type: ignore[arg-type]
        targets=[
            BulkEmailTarget(
                type=BulkEmailTargetType.INTEREST,
                to_all=False,
                values=["3"],
            ),
            BulkEmailTarget(
                type=BulkEmailTargetType.HASHTAG,
                to_all=False,
                values=["ai"],
            ),
            BulkEmailTarget(
                type=BulkEmailTargetType.USER,
                to_all=False,
                values=[str(uuid4())],
            ),
        ],
    )

    types = [item[0] for item in captured["targets"]]
    assert types == [
        NotificationTargetType.interests,
        NotificationTargetType.hashtags,
        NotificationTargetType.users,
    ]

@pytest.mark.asyncio
async def test_resolve_excludes_users_with_bulk_email_disabled(monkeypatch):
    enabled_user = uuid4()
    disabled_user = uuid4()

    async def _list_all(db, *, is_alumni=False):
        return [enabled_user, disabled_user]

    async def _fake_pref_filter(db, user_ids, *, preference):
        assert preference == "bulk_email"
        return [uid for uid in user_ids if uid == enabled_user]

    monkeypatch.setattr(
        "apps.bulk_send.recipient_resolution._list_email_eligible_user_ids",
        _list_all,
    )
    monkeypatch.setattr(
        "apps.notifications.repositories.notification_repository.filter_users_eligible_for_email_preference",
        _fake_pref_filter,
    )

    result = await resolve_bulk_email_user_ids(
        db=object(),  # type: ignore[arg-type]
        targets=[],
        is_alumni=False,
    )
    assert result == [enabled_user]

    audience = await resolve_bulk_email_audience(
        db=object(),  # type: ignore[arg-type]
        targets=[],
        is_alumni=False,
    )
    assert audience.eligible_user_ids == [enabled_user]
    assert audience.preference_excluded_user_ids == [disabled_user]


def test_bulk_email_preference_opt_out_message_single_with_identity():
    assert bulk_email_preference_opt_out_message(
        excluded_count=1,
        email="user@example.com",
    ) == (
        "Bulk email cannot be sent: user@example.com has turned off "
        "bulk email notifications"
    )


def test_bulk_email_preference_opt_out_message_multiple():
    assert (
        bulk_email_preference_opt_out_message(excluded_count=3)
        == "All selected recipients have turned off bulk email notifications"
    )


@pytest.mark.asyncio
async def test_resolve_filtered_campaign_respects_bulk_email_preference(monkeypatch):
    enabled_user = uuid4()
    disabled_user = uuid4()

    async def _fake_resolve_topic(db, *, targets):
        return [enabled_user, disabled_user]

    async def _fake_filter(db, user_ids, *, is_alumni=False):
        return user_ids

    async def _fake_pref_filter(db, user_ids, *, preference):
        return [uid for uid in user_ids if uid == enabled_user]

    monkeypatch.setattr(
        "apps.bulk_send.recipient_resolution.resolve_topic_recipients",
        _fake_resolve_topic,
    )
    monkeypatch.setattr(
        "apps.bulk_send.recipient_resolution._filter_email_eligible_user_ids",
        _fake_filter,
    )
    monkeypatch.setattr(
        "apps.notifications.repositories.notification_repository.filter_users_eligible_for_email_preference",
        _fake_pref_filter,
    )

    result = await resolve_bulk_email_user_ids(
        db=object(),  # type: ignore[arg-type]
        targets=[
            BulkEmailTarget(
                type=BulkEmailTargetType.MAJOR,
                to_all=False,
                values=["1"],
            )
        ],
    )
    assert result == [enabled_user]


@pytest.mark.asyncio
async def test_resolve_explicit_user_ids_respect_bulk_email_preference(monkeypatch):
    enabled_user = uuid4()
    disabled_user = uuid4()

    async def _fake_resolve_topic(db, *, targets):
        return [enabled_user, disabled_user]

    async def _fake_filter(db, user_ids, *, is_alumni=False):
        return user_ids

    async def _fake_pref_filter(db, user_ids, *, preference):
        return [uid for uid in user_ids if uid == enabled_user]

    monkeypatch.setattr(
        "apps.bulk_send.recipient_resolution.resolve_topic_recipients",
        _fake_resolve_topic,
    )
    monkeypatch.setattr(
        "apps.bulk_send.recipient_resolution._filter_email_eligible_user_ids",
        _fake_filter,
    )
    monkeypatch.setattr(
        "apps.notifications.repositories.notification_repository.filter_users_eligible_for_email_preference",
        _fake_pref_filter,
    )

    result = await resolve_bulk_email_user_ids(
        db=object(),  # type: ignore[arg-type]
        targets=[
            BulkEmailTarget(
                type=BulkEmailTargetType.USER,
                to_all=False,
                values=[str(enabled_user), str(disabled_user)],
            )
        ],
    )
    assert result == [enabled_user]

