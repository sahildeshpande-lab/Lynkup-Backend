"""Signed Admin auth dependencies for Category C protected Admin APIs.

Builds on ``require_admin_signed_request`` (Origin + JWT + session + RSA +
timestamp + nonce) then applies role checks.
"""

from __future__ import annotations

from fastapi import Depends

from apps.accounts.db_models import User
from apps.administration.dependencies.request_signing import require_admin_signed_request
from common.exceptions import ApiError


async def require_signed_admin(
    user: User = Depends(require_admin_signed_request),
) -> User:
    return user


async def require_signed_moderator(
    user: User = Depends(require_admin_signed_request),
) -> User:
    if user.role not in ("moderator", "superadmin"):
        raise ApiError("Insufficient permissions")
    return user


async def require_signed_moderator_or_viewer(
    user: User = Depends(require_admin_signed_request),
) -> User:
    if user.role not in ("moderator", "superadmin", "viewer"):
        raise ApiError("Insufficient permissions")
    return user


async def require_signed_superadmin(
    user: User = Depends(require_admin_signed_request),
) -> User:
    if user.role != "superadmin":
        raise ApiError("Insufficient permissions")
    return user
