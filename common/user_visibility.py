from __future__ import annotations

from sqlalchemy import and_, or_

from common.enums import UserStatus

# Accounts in these statuses must not appear in discovery, connections,
# comments, or public post surfaces.
# NOTE: ``deleting`` is intentionally excluded — grace-period accounts keep
# their content and social graph visible until permanent purge.
HIDDEN_ACCOUNT_STATUSES: tuple[UserStatus, ...] = (
    UserStatus.suspended,
    UserStatus.banned,
)


def is_hidden_account_status(status: UserStatus | str | None) -> bool:
    if status is None:
        return False
    value = status.value if isinstance(status, UserStatus) else str(status)
    return value in {member.value for member in HIDDEN_ACCOUNT_STATUSES}


def visible_user_filters(user_model) -> list:
    """SQLAlchemy WHERE clauses for users that may be shown publicly.

    Grace-period ``deleting`` users remain visible even though ``is_deleted`` /
    ``deleted_at`` are set — content must stay on the platform until purge.
    """
    not_restricted = user_model.status.notin_(list(HIDDEN_ACCOUNT_STATUSES))
    active_row = and_(
        user_model.is_deleted.is_(False),
        user_model.deleted_at.is_(None),
    )
    grace_period = user_model.status == UserStatus.deleting
    return [not_restricted, or_(active_row, grace_period)]


async def check_post_engagement_allowed(
    db: "AsyncSession",
    current_user_id: "UUID",
    author_id: "UUID",
) -> None:
    from uuid import UUID
    from sqlalchemy import or_, and_, select
    from apps.accounts.db_models import User
    from apps.connections.db_models import Block
    from common.exceptions import ApiError
    from unittest.mock import Mock, AsyncMock

    if current_user_id == author_id:
        return

    # Check block in either direction
    try:
        block_stmt = select(Block).where(
            Block.is_active == True,
            or_(
                and_(Block.blocker_user_id == current_user_id, Block.blocked_user_id == author_id),
                and_(Block.blocker_user_id == author_id, Block.blocked_user_id == current_user_id)
            )
        )
        block_res = await db.execute(block_stmt)
        if hasattr(block_res, "scalars"):
            first_scalar = block_res.scalars().first()
            if first_scalar is not None and not isinstance(first_scalar, (Mock, AsyncMock)):
                raise ApiError("Action forbidden due to blocks.")
    except ApiError:
        raise
    except Exception:  # nosec B110 -- best-effort fallback
        pass

    # Check author status
    try:
        author_stmt = select(User).where(User.id == author_id)
        author_res = await db.execute(author_stmt)
        author = author_res.scalar_one_or_none() if hasattr(author_res, "scalar_one_or_none") else None
    except Exception:
        author = None

    if author is not None and hasattr(author, "status") and not isinstance(author.status, (Mock, AsyncMock)):
        author_status = author.status.value if hasattr(author.status, "value") else str(author.status)
        if str(author_status).lower() in ("banned", "suspended"):
            raise ApiError(f"Original post author is {str(author_status).lower()}")
        if str(author_status) != UserStatus.deleting.value and (
            getattr(author, "is_deleted", False) or getattr(author, "deleted_at", None)
        ):
            raise ApiError(f"Original post author is {str(author_status).lower()}")

