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
