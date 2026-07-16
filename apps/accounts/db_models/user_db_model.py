from datetime import datetime, timezone
from typing import Optional, List
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Column, DateTime, String, Integer, text
from sqlalchemy.orm import relationship
from sqlmodel import Field, SQLModel, Relationship

from common.enums import OnboardingStatus, UserStatus, RegistrationType


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class UserRole(SQLModel, table=True):
    __tablename__ = "user_roles"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id")
    role_id: UUID = Field(foreign_key="roles.id")
    assigned_at: datetime = Field(default_factory=datetime.utcnow)

    user: "User" = Relationship(
        sa_relationship=relationship("User", back_populates="roles")
    )
    role: "Role" = Relationship(
        sa_relationship=relationship("Role", back_populates="users", lazy="selectin")
    )


class RolePermission(SQLModel, table=True):
    __tablename__ = "role_permissions"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    role_id: UUID = Field(foreign_key="roles.id")
    permission_id: UUID = Field(foreign_key="permissions.id")

    role: "Role" = Relationship(
        sa_relationship=relationship("Role", back_populates="permissions")
    )
    permission: "Permission" = Relationship(
        sa_relationship=relationship("Permission", back_populates="roles", lazy="selectin")
    )


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    firebase_uid: Optional[str] = Field(default=None, sa_column=Column(String(128), nullable=True, unique=True, index=True))
    email: str = Field(sa_column=Column(String(320), nullable=False, unique=True, index=True))
    password_hash: Optional[str] = Field(default=None, sa_column=Column(String(255), nullable=True))
    email_otp: Optional[str] = Field(default=None, sa_column=Column(String(16), nullable=True))
    email_otp_created_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    status: UserStatus = Field(default=UserStatus.pending, index=True)
    email_verified_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    registration_type: RegistrationType = Field(default=RegistrationType.email, sa_column=Column(String(20), nullable=False, server_default="email"))
    onboarding_status: OnboardingStatus = Field(default=OnboardingStatus.not_started, index=True)
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    deleted_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    purge_after: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    is_deleted: bool = Field(default=False, sa_column=Column(Boolean, server_default=text("false"), nullable=False))
    last_login_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))


    roles: List[UserRole] = Relationship(
        sa_relationship=relationship("UserRole", back_populates="user", cascade="all, delete-orphan", lazy="selectin")
    )
    bookmarks: List["Bookmark"] = Relationship(
        sa_relationship=relationship("Bookmark", back_populates="user", cascade="all, delete-orphan", lazy="selectin")
    )
    share_events: List["ShareEvent"] = Relationship(
        sa_relationship=relationship("ShareEvent", back_populates="user", cascade="all, delete-orphan", lazy="selectin")
    )
    comments: List["Comment"] = Relationship(
        sa_relationship=relationship("Comment", back_populates="author", cascade="all, delete-orphan", lazy="selectin")
    )
    comment_reactions: List["CommentReaction"] = Relationship(
        sa_relationship=relationship("CommentReaction", back_populates="user", cascade="all, delete-orphan", lazy="selectin")
    )


    @property
    def role(self) -> str:
        if hasattr(self, "roles") and self.roles:
            for ur in self.roles:
                if ur.role:
                    return ur.role.name
        return "user"

    @role.setter
    def role(self, value: str) -> None:
        if not hasattr(self, "roles") or self.roles is None:
            self.roles = []
        if not any(ur.role.name == value for ur in self.roles if ur.role):
            role_obj = Role(name=value, description=f"{value} role")
            self.roles.append(UserRole(role=role_obj))

class Role(SQLModel, table=True):
    __tablename__ = "roles"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(unique=True, index=True)
    description: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

    users: List[UserRole] = Relationship(
        sa_relationship=relationship("UserRole", back_populates="role", cascade="all, delete-orphan", lazy="selectin")
    )
    permissions: List[RolePermission] = Relationship(
        sa_relationship=relationship("RolePermission", back_populates="role", cascade="all, delete-orphan", lazy="selectin")
    )
    


class Permission(SQLModel, table=True):
    __tablename__ = "permissions"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(unique=True, index=True)
    resource: str
    action: str

    roles: List[RolePermission] = Relationship(
        sa_relationship=relationship("RolePermission", back_populates="permission", cascade="all, delete-orphan", lazy="selectin")
    )


class PasswordResetToken(SQLModel, table=True):
    __tablename__ = "password_reset_tokens"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", index=True)
    token: str = Field(default_factory=lambda: str(uuid4()), sa_column=Column(String(36), index=True, unique=True, nullable=False))
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    used_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))

