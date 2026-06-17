from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from apps.accounts.db_models import (
    User,
    UserRole,
    Role,
    Permission,
    RolePermission,
)
from core.security.auth import get_current_user
from core.database.session import get_session


async def require_role(
    role_name: str,
    user: User = Depends(get_current_user),
) -> User:
    """FastAPI dependency that ensures the authenticated user has the given role.

    Args:
        role_name: The name of the role required (e.g. "superadmin").
        user: The current user, injected via ``get_current_user``.
    Raises:
        HTTPException: 403 if the user does not have the required role.
    Returns:
        The validated ``User`` instance.
    """
    if user.role != role_name:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient role",
        )
    return user


async def require_permission(
    action: str,
    resource: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> User:
    """FastAPI dependency that ensures the user has a permission matching ``action`` and ``resource``.

    The check walks the ``User -> UserRole -> Role -> RolePermission -> Permission`` chain.
    """
    # Subquery to fetch role ids assigned to the user
    role_ids_stmt = (
        select(UserRole.role_id).where(UserRole.user_id == user.id)
    )

    stmt = (
        select(Permission)
        .join(RolePermission, Permission.id == RolePermission.permission_id)
        .join(Role, Role.id == RolePermission.role_id)
        .where(
            Role.id.in_(role_ids_stmt),
            Permission.action == action,
            Permission.resource == resource,
        )
    )

    result = await db.execute(stmt)
    perm = result.scalar_one_or_none()
    if perm is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permission",
        )
    return user
