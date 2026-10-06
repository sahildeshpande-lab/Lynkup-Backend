from apps.feed.services.post_service import (
    _normalize_list_state,
    _query_states_for_list,
    _reject_other_user_private_post_states,
)
from common.enums import FEED_VISIBLE_POST_STATES, OWNER_VISIBLE_POST_STATES, PostState
from fastapi import HTTPException
from types import SimpleNamespace
import uuid
import pytest


def test_normalize_list_state_defaults_to_all() -> None:
    assert _normalize_list_state(None) == "all"
    assert _normalize_list_state("") == "all"
    assert _normalize_list_state("  ") == "all"
    assert _normalize_list_state("all") == "all"
    assert _normalize_list_state("published") == "published"
    assert _normalize_list_state("rejected") == "rejected"


def test_query_states_all_owner_vs_visitor() -> None:
    assert _query_states_for_list("all", is_owner=True) == OWNER_VISIBLE_POST_STATES
    assert _query_states_for_list("all", is_owner=False) == FEED_VISIBLE_POST_STATES


def test_query_states_published_is_feed_visible() -> None:
    assert _query_states_for_list("published", is_owner=True) == FEED_VISIBLE_POST_STATES
    assert _query_states_for_list("published", is_owner=False) == FEED_VISIBLE_POST_STATES
    assert _query_states_for_list(PostState.published, is_owner=True) == FEED_VISIBLE_POST_STATES


def test_query_states_flagged_includes_processing() -> None:
    assert _query_states_for_list("flagged", is_owner=True) == (
        PostState.flagged,
        PostState.processing,
    )
    assert _query_states_for_list(PostState.flagged, is_owner=False) == (
        PostState.flagged,
        PostState.processing,
    )


def test_query_states_processing_and_draft_are_exact() -> None:
    assert _query_states_for_list("processing", is_owner=True) == PostState.processing
    assert _query_states_for_list("draft", is_owner=True) == PostState.draft
    assert _query_states_for_list("rejected", is_owner=True) == PostState.rejected
    assert _query_states_for_list("rejected", is_owner=False) == PostState.rejected
    assert _query_states_for_list(PostState.rejected, is_owner=False) == PostState.rejected


def test_reject_other_user_flagged_or_processing() -> None:
    current = SimpleNamespace(id=uuid.uuid4(), role="user")
    other = uuid.uuid4()
    with pytest.raises(HTTPException) as exc:
        _reject_other_user_private_post_states(
            current_user=current,
            target_user_id=other,
            requested_state="flagged",
        )
    assert exc.value.status_code == 403

    with pytest.raises(HTTPException) as exc:
        _reject_other_user_private_post_states(
            current_user=current,
            target_user_id=other,
            requested_state=PostState.processing,
        )
    assert exc.value.status_code == 403


@pytest.mark.parametrize("role", ["moderator", "viewer", "superadmin"])
def test_staff_can_view_other_user_flagged_or_processing(role: str) -> None:
    current = SimpleNamespace(id=uuid.uuid4(), role=role)
    other = uuid.uuid4()
    _reject_other_user_private_post_states(
        current_user=current,
        target_user_id=other,
        requested_state="flagged",
    )
    _reject_other_user_private_post_states(
        current_user=current,
        target_user_id=other,
        requested_state="processing",
    )
    _reject_other_user_private_post_states(
        current_user=current,
        target_user_id=other,
        requested_state="rejected",
    )
    _reject_other_user_private_post_states(
        current_user=current,
        target_user_id=current.id,
        requested_state="rejected",
    )


def test_regular_user_cannot_request_rejected() -> None:
    current = SimpleNamespace(id=uuid.uuid4(), role="user")
    with pytest.raises(HTTPException) as exc:
        _reject_other_user_private_post_states(
            current_user=current,
            target_user_id=current.id,
            requested_state="rejected",
        )
    assert exc.value.status_code == 403

    with pytest.raises(HTTPException) as exc:
        _reject_other_user_private_post_states(
            current_user=current,
            target_user_id=uuid.uuid4(),
            requested_state=PostState.rejected,
        )
    assert exc.value.status_code == 403


def test_allow_own_user_flagged_or_published_for_other() -> None:
    current_id = uuid.uuid4()
    current = SimpleNamespace(id=current_id, role="user")
    _reject_other_user_private_post_states(
        current_user=current,
        target_user_id=current_id,
        requested_state="flagged",
    )
    _reject_other_user_private_post_states(
        current_user=current,
        target_user_id=uuid.uuid4(),
        requested_state="published",
    )
    _reject_other_user_private_post_states(
        current_user=current,
        target_user_id=uuid.uuid4(),
        requested_state="all",
    )
