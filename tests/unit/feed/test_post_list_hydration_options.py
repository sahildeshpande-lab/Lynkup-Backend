from __future__ import annotations

from apps.feed.repositories.post_repository import _post_list_hydration_load_options


def test_post_list_hydration_load_options_returns_two_loader_options() -> None:
    options = _post_list_hydration_load_options()
    assert len(options) == 2
