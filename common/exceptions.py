from __future__ import annotations


class ApiError(Exception):
    """Business, auth, or validation failure returned as JSON with status=false."""

    def __init__(self, message: str, *, cleanup_firebase: bool = True) -> None:
        self.message = message
        self.cleanup_firebase = cleanup_firebase
        super().__init__(message)
