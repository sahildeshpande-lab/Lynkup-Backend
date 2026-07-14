from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .config import DatabaseSettings, settings

if TYPE_CHECKING:
    from .session import async_session_factory, engine, get_session


def __getattr__(name: str) -> Any:
    if name in {"async_session_factory", "engine", "get_session"}:
        from . import session

        return getattr(session, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "DatabaseSettings",
    "async_session_factory",
    "engine",
    "get_session",
    "settings",
]
