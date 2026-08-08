from apps.feed.services.post_service import _query_states_for_list, _reject_other_user_private_post_states
from common.enums import FEED_VISIBLE_POST_STATES, PostState
from fastapi import HTTPException
from types import SimpleNamespace
import uuid
import pytest


def test_query_states_default_published_is_feed_visible() -> None:
    assert _query_states_for_list(PostState.published, is_owner=True) == FEED_VISIBLE_POST_STATES
    assert _query_states_for_list(PostState.published, is_owner=False) == FEED_VISIBLE_POST_STATES


def test_query_states_flagged_and_processing_are_exact() -> None:
    assert _query_states_for_list(PostState.flagged, is_owner=True) == PostState.flagged
    assert _query_states_for_list(PostState.processing, is_owner=True) == PostState.processing


def test_query_states_draft_is_exact() -> None:
    assert _query_states_for_list(PostState.draft, is_owner=True) == PostState.draft


def test_reject_other_user_flagged_or_processing() -> None:
    current = SimpleNamespace(id=uuid.uuid4())
    other = uuid.uuid4()
    with pytest.raises(HTTPException) as exc:
        _reject_other_user_private_post_states(
            current_user=current,
            target_user_id=other,
            requested_state=PostState.flagged,
        )
    assert exc.value.status_code == 403

    with pytest.raises(HTTPException) as exc:
        _reject_other_user_private_post_states(
            current_user=current,
            target_user_id=other,
            requested_state=PostState.processing,
        )
    assert exc.value.status_code == 403


def test_allow_own_user_flagged_or_published_for_other() -> None:
    current_id = uuid.uuid4()
    current = SimpleNamespace(id=current_id)
    _reject_other_user_private_post_states(
        current_user=current,
        target_user_id=current_id,
        requested_state=PostState.flagged,
    )
    _reject_other_user_private_post_states(
        current_user=current,
        target_user_id=uuid.uuid4(),
        requested_state=PostState.published,
    )
