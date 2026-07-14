from __future__ import annotations

from apps.search.repositories import post_search_repository as repo


def test_normalize_hashtag():
    assert repo._normalize_hashtag("#AI") == "ai"
    assert repo._normalize_hashtag(" machine-learning ") == "machine-learning"
