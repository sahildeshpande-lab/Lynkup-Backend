from __future__ import annotations

import uuid

from apps.search.repositories import post_search_repository as repo


def test_normalize_hashtag():
    assert repo._normalize_hashtag("#AI") == "ai"
    assert repo._normalize_hashtag(" machine-learning ") == "machine-learning"


def test_try_parse_uuid():
    value = str(uuid.uuid4())
    assert repo._try_parse_uuid(value) == uuid.UUID(value)
    assert repo._try_parse_uuid("not-a-uuid") is None
    assert repo._try_parse_uuid("#ai") is None


def test_resolve_edu_level():
    assert repo._resolve_edu_level("1") == "Bachelors"
    assert repo._resolve_edu_level("Bachelors") == "Bachelors"
    assert repo._resolve_edu_level("masters") == "Masters"
    assert repo._resolve_edu_level("99") is None
