from __future__ import annotations


class ApiError(Exception):
    """Business, auth, or validation failure returned as JSON with status=false."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)
