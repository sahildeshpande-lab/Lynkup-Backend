from __future__ import annotations

from enum import Enum


class UserStatus(str, Enum):
    pending = "pending"
    active = "active"
    suspicious_review = "suspicious_review"
    suspended = "suspended"
    banned = "banned"
    deleting = "deleting"


class OnboardingStatus(str, Enum):
    pending = "pending"
    not_started = "not_started"
    in_progress = "in_progress"
    completed = "completed"


class ProfileVisibility(str, Enum):
    public = "public"
    connections_only = "connections_only"
    private = "private"


from typing import Literal

Role = Literal["user", "moderator", "viewer", "superadmin"]
SocialProvider = Literal["google", "apple"]
EducationLevel = Literal["Bachelors", "Masters", "Doctorate", "Postdoctoral", "JD", "MD"]


class RegistrationType(str, Enum):
    email = "email"
    google = "google"
    apple = "apple"

