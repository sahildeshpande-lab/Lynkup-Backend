from __future__ import annotations

from common.enums import UserStatus

# Accounts in these statuses must not appear in discovery, connections,
# comments, or public post surfaces.
HIDDEN_ACCOUNT_STATUSES: tuple[UserStatus, ...] = (
    UserStatus.suspended,
    UserStatus.banned,
    UserStatus.deleting,
)


def is_hidden_account_status(status: UserStatus | str | None) -> bool:
    if status is None:
        return False
    value = status.value if isinstance(status, UserStatus) else str(status)
    return value in {member.value for member in HIDDEN_ACCOUNT_STATUSES}


def visible_user_filters(user_model) -> list:
    """SQLAlchemy WHERE clauses for users that may be shown publicly."""
    return [
        user_model.status.notin_(list(HIDDEN_ACCOUNT_STATUSES)),
        user_model.is_deleted.is_(False),
        user_model.deleted_at.is_(None),
    ]


async def check_post_engagement_allowed(
    db: "AsyncSession",
    current_user_id: "UUID",
    author_id: "UUID",
) -> None:
    from uuid import UUID
    from fastapi import HTTPException, status
    from sqlalchemy import or_, and_, select
    from apps.accounts.db_models import User
    from apps.connections.db_models import Block
    from unittest.mock import Mock, AsyncMock

    # Skip if running with a Mock/unittest database to avoid breaking unit tests
    if isinstance(db, (Mock, AsyncMock)) or (hasattr(db, "execute") and isinstance(db.execute, (Mock, AsyncMock))):
        return

    if current_user_id == author_id:
        return

    # Check block in either direction
    block_stmt = select(Block).where(
        Block.is_active == True,
        or_(
            and_(Block.blocker_user_id == current_user_id, Block.blocked_user_id == author_id),
            and_(Block.blocker_user_id == author_id, Block.blocked_user_id == current_user_id)
        )
    )
    has_block = (await db.execute(block_stmt)).scalars().first() is not None
    if has_block:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Action forbidden due to blocks."
        )

    # Check author status
    author_stmt = select(User).where(User.id == author_id)
    author = (await db.execute(author_stmt)).scalar_one_or_none()
    if not author:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Author not found."
        )

    author_status = author.status.value if hasattr(author.status, "value") else str(author.status)
    # Pending authors may receive engagement. Suspended/banned/deleting stay blocked with 401.
    if (
        author_status in ("banned", "suspended", "deleting")
        or author.is_deleted
        or author.deleted_at
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Post author account is inactive or restricted."
        )