from __future__ import annotations

from apps.accounts.db_models import ConsentRecord, SecurityEvent, RefreshToken, User, UserInstallation, TransactionalEmailLog, Role, Permission, UserRole, RolePermission
from apps.profiles.db_models import Country, Profile, AcademicInterest, University

__all__ = [
    "ConsentRecord",
    "Country",
    "Profile",
    "AcademicInterest",
    "SecurityEvent",
    "RefreshToken",
    "University",
    "User",
    "UserInstallation",
    "TransactionalEmailLog",
    "Role",
    "Permission",
    "UserRole",
    "RolePermission",
]
