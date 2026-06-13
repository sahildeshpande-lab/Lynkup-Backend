from __future__ import annotations

from apps.accounts.db_models import ConsentRecord, SecurityEvent, RefreshToken, User, UserIdentity, UserInstallation, TransactionalEmailLog, Role, Permission, UserRole, RolePermission
from apps.profiles.db_models import AcademicProgram, Country, Profile, ProfileInterest, University

__all__ = [
    "AcademicProgram",
    "ConsentRecord",
    "Country",
    "Profile",
    "ProfileInterest",
    "SecurityEvent",
    "RefreshToken",
    "University",
    "User",
    "UserIdentity",
    "UserInstallation",
    "TransactionalEmailLog",
    "Role",
    "Permission",
    "UserRole",
    "RolePermission",
]
