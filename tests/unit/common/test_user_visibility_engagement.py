from __future__ import annotations

import pytest
from entrypoints.api import _api_error_status_code, _is_account_status_message


def test_suspended_user_own_status_returns_401() -> None:
    # When User B (suspended user) is interacting, their own account status is returned
    assert _is_account_status_message("Your account is suspended") is True
    assert _api_error_status_code("Your account is suspended") == 401

    assert _is_account_status_message("Your account is banned") is True
    assert _api_error_status_code("Your account is banned") == 401

    assert _is_account_status_message("Your account is deleting") is True
    assert _api_error_status_code("Your account is deleting") == 401


def test_suspended_author_engagement_returns_200() -> None:
    # When User A interacts with suspended User B's post, the error refers to the author
    # It must NOT return 401 (which would force logout User A)
    assert _is_account_status_message("Account is currently suspended") is False
    assert _api_error_status_code("Account is currently suspended") == 200

    assert _is_account_status_message("Account is currently banned") is False
    assert _api_error_status_code("Account is currently banned") == 200

    assert _is_account_status_message("Account is currently unavailable") is False
    assert _api_error_status_code("Account is currently unavailable") == 200


def test_account_exists_returns_401_and_account_missing_returns_401() -> None:
    assert _api_error_status_code("Account already exists") == 401
    assert _api_error_status_code("Account doesn't exist") == 401
    assert _api_error_status_code("This user is not allowed") == 401
