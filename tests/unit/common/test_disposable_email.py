"""Unit tests for disposable-email validation helper."""

from __future__ import annotations

import pytest

from common.email_validation import (
    DISPOSABLE_EMAIL_NOT_ALLOWED_MESSAGE,
    validate_disposable_email,
)
from common.exceptions import ApiError


def test_disabled_allows_disposable_email() -> None:
    validate_disposable_email("temporary@mailinator.com", is_enabled=False)


def test_disabled_allows_normal_email() -> None:
    validate_disposable_email("user@gmail.com", is_enabled=False)


def test_enabled_rejects_disposable_email() -> None:
    with pytest.raises(ApiError) as exc_info:
        validate_disposable_email("temporary@mailinator.com", is_enabled=True)
    assert str(exc_info.value.message) == DISPOSABLE_EMAIL_NOT_ALLOWED_MESSAGE


def test_enabled_allows_normal_email() -> None:
    validate_disposable_email("user@example.com", is_enabled=True)


def test_enabled_rejects_mixed_case_disposable_domain() -> None:
    with pytest.raises(ApiError) as exc_info:
        validate_disposable_email("test@Mailinator.COM", is_enabled=True)
    assert str(exc_info.value.message) == DISPOSABLE_EMAIL_NOT_ALLOWED_MESSAGE


@pytest.mark.parametrize(
    "bad_email",
    [
        "",
        "   ",
        "nodomain",
        "missing-domain@",
    ],
)
def test_enabled_rejects_invalid_or_missing_domain(bad_email: str) -> None:
    with pytest.raises(ApiError) as exc_info:
        validate_disposable_email(bad_email, is_enabled=True)
    assert str(exc_info.value.message) == DISPOSABLE_EMAIL_NOT_ALLOWED_MESSAGE


def test_disabled_allows_invalid_or_missing_domain() -> None:
    # When the flag is off, the helper must not change registration behavior.
    validate_disposable_email("nodomain", is_enabled=False)
    validate_disposable_email("", is_enabled=False)
