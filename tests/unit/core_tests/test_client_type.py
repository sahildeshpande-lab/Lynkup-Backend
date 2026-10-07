"""Unit tests for auth-derived client type tagging."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.request_signing import CLIENT_TYPE_MOBILE, CLIENT_TYPE_WEB, mark_client_type
from core.request_signing import client_type as ct


def test_mark_client_type_web_and_mobile():
    request = SimpleNamespace(state=SimpleNamespace())
    assert mark_client_type(request, CLIENT_TYPE_WEB) == "web"
    assert request.state.client_type == "web"

    assert mark_client_type(request, CLIENT_TYPE_MOBILE) == "mobile"
    assert request.state.client_type == "mobile"


def test_mark_client_type_rejects_unknown():
    request = SimpleNamespace(state=SimpleNamespace())
    with pytest.raises(ValueError, match="unsupported client_type"):
        mark_client_type(request, "desktop")


def test_allowed_client_types_constant():
    assert ct.ALLOWED_CLIENT_TYPES == frozenset({"web", "mobile"})
