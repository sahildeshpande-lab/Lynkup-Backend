from __future__ import annotations

import uuid

import pytest

from apps.engagement.schemas import UpsertPostReactionRequest
from common.enums import ReactionType


def test_upsert_post_reaction_request_exposes_enum_values_in_schema():
    schema = UpsertPostReactionRequest.model_json_schema()
    reaction_field = schema["properties"]["reaction_type"]
    assert reaction_field["description"]
    assert "like" in reaction_field["description"]
    assert "celebrate" in reaction_field["description"]
    assert set(reaction_field["enum"]) >= {None, *[rt.value for rt in ReactionType]}


def test_upsert_post_reaction_request_accepts_enum_and_string():
    payload = UpsertPostReactionRequest(post_id=uuid.uuid4(), reaction_type="LIKE")
    assert payload.reaction_type == ReactionType.like

    payload_enum = UpsertPostReactionRequest(post_id=uuid.uuid4(), reaction_type=ReactionType.support)
    assert payload_enum.reaction_type == ReactionType.support


def test_upsert_post_reaction_request_rejects_invalid_enum():
    with pytest.raises(ValueError, match="Invalid reaction_type"):
        UpsertPostReactionRequest(post_id=uuid.uuid4(), reaction_type="invalid")
