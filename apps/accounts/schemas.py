from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator

from common.enums import ProfileVisibility
from common.enums import Role, SocialProvider


class ApiResponse(BaseModel):
    status: bool = True
    message: str = "success"
    data: Any | None = None


class SocialAuthRequest(BaseModel):
    provider: SocialProvider
    idToken: str
    email: Optional[EmailStr] = None
    firstName: Optional[str] = None
    lastName: Optional[str] = None
    fullName: Optional[str] = None
    profilePhotoUrl: Optional[str] = None


class EmailSignupRequest(BaseModel):
    firstName: str
    lastName: str
    email: EmailStr
    password: str = Field(min_length=8, max_length=20)
    role: Role
    firebaseId: str
    device_id: str

    @field_validator("device_id")
    @classmethod
    def normalize_device_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("device_id cannot be blank")
        return normalized

    @field_validator("firstName", "lastName")
    @classmethod
    def validate_names(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("names cannot be blank")
        return value.strip()

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        if not value or not value.strip():
            raise ValueError("email cannot be blank")
        return value.lower().strip()

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("password cannot be blank")
        if not any(char.isupper() for char in value):
            raise ValueError("password must contain at least one uppercase letter")
        if not any(char.isdigit() for char in value):
            raise ValueError("password must contain at least one number")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    firebaseId: str
    device_id: str

    @field_validator("device_id")
    @classmethod
    def normalize_device_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("device_id cannot be blank")
        return normalized

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        if not value or not value.strip():
            raise ValueError("email cannot be blank")
        return value.lower().strip()

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("password cannot be blank")
        return value


EmailLoginRequest = LoginRequest




class OtpVerifyRequest(BaseModel):
    email: EmailStr
    otp: str
    firebaseId: str


class ResendOtpRequest(BaseModel):
    email: EmailStr
    firebaseId: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr
    firebaseId: Optional[str] = None


class RefreshTokenRequest(BaseModel):
    refreshToken: str


class LogoutRequest(BaseModel):
    device_id: str = Field(min_length=1)

    @field_validator("device_id")
    @classmethod
    def normalize_device_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("device_id cannot be blank")
        return normalized


class NotificationPreferences(BaseModel):
    email: bool = True
    push: bool = True
    inApp: bool = True


class AuthUserResponse(BaseModel):
    id: str | None = None
    firstName: str
    lastName: str
    email: str
    role: Role
    firebase_uid: str | None = None
    profilePhoto_url: str | None = None
    bannerPhotoUrl: str | None = None
    status: str = "pending"
    university: str | None = None
    major: str | None = None
    minor: str | None = None
    educationLevel: str | None = None
    bio: str | None = None
    academicInterests: list[str] = Field(default_factory=list)
    profileVisibility: ProfileVisibility = ProfileVisibility.public
    completenessScore: int = 33
    notificationPreferences: NotificationPreferences = Field(default_factory=NotificationPreferences)
    isEmailVerified: bool = False
    email_verified_at: datetime | None = None
    email_otp_created_at: datetime | None = None
    referenceCode: str | None = None
    invitationCode: str | None = None
    invitationDeepLinkUrl: str | None = None
    invitationWebUrl: str | None = None
    onlinePresence: bool = False
    createdAt: datetime | None = None
    updatedAt: datetime | None = None
    posts_count: int|None =None ,
    followers_count: int|None =None ,
    following_count: int|None =None,
    is_onboarding_completed: bool = False
    is_deleted: bool = False


class UserBaseResponse(BaseModel):
    id: str | None = None
    firstName: str = ""
    lastName: str = ""
    email: str = ""
    role: Role = "user"
    firebase_uid: str | None = None
    profilePhoto_url: str | None = None
    bannerPhotoUrl: str | None = None
    status: str = ""
    university: str | None = None
    major: str | None = None
    minor: str | None = None
    educationLevel: str | None = None
    bio: str | None = None
    academicInterests: list[str] = Field(default_factory=list)
    profileVisibility: str = ""
    completenessScore: int = 0
    notificationPreferences: NotificationPreferences = Field(default_factory=NotificationPreferences)
    isEmailVerified: bool = False
    email_verified_at: datetime | None = None
    email_otp_created_at: datetime | None = None
    referenceCode: str = ""
    invitationCode: str | None = None
    invitationDeepLinkUrl: str | None = None
    invitationWebUrl: str | None = None
    onlinePresence: bool = False
    createdAt: datetime | None = None
    updatedAt: datetime | None = None
    posts_count: int | None = None
    followers_count: int | None = None
    following_count: int | None = None
    is_onboarding_completed: bool = False
    is_deleted: bool = False


class UserAuthSessionResponse(BaseModel):
    user: UserBaseResponse
    emailSent: bool = False


class UserAuthResponse(ApiResponse):
    data: UserAuthSessionResponse | None = None


class AdminAuthSessionResponse(BaseModel):
    accessToken: str
    refreshToken: str
    user: UserBaseResponse
    emailSent: bool = False


class AdminAuthResponse(ApiResponse):
    data: AdminAuthSessionResponse | None = None


class AuthSessionResponse(BaseModel):
    user: AuthUserResponse
    emailSent: bool = False
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "bearer"


class LogoutRequest(BaseModel):
    firebaseId: str
    device_id: str

class RefreshSessionResponse(BaseModel):
    refreshToken: str
    user: UserBaseResponse


class PaginationParams(BaseModel):
    page: int = 1
    pageSize: int = 20


class ResetPasswordRequest(BaseModel):
    token: str | None = None
    firebaseId: str | None = None
    new_password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_at: datetime | None = None

class UserChangePasswordRequest(BaseModel):
    firebaseId: str
    current_password: str
    new_password: str = Field(min_length=8)

    @field_validator("new_password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if not any(char.isupper() for char in value):
            raise ValueError("password must contain at least one uppercase letter")
        if not any(char.isdigit() for char in value):
            raise ValueError("password must contain at least one number")
        return value
