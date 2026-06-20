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

class EducationLevel(str, Enum):
    
    bachelors = "Bachelors"
    masters = "Masters"
    doctorate = "Doctorate"
    postdoctoral = "Postdoctoral"
    jd = "JD"
    md = "MD"

    @property
    def id(self) -> int:
        return list(type(self)).index(self) + 1

    @classmethod
    def from_id(cls, value: int | str) -> "EducationLevel":
        level_id = int(value)
        for level in cls:
            if level.id == level_id:
                return level
        raise ValueError(f"Unknown education level id: {value}")


from typing import Literal

Role = Literal["user", "moderator", "viewer", "superadmin"]
SocialProvider = Literal["google", "apple"]


class RegistrationType(str, Enum):
    email = "email"
    google = "google"
    apple = "apple"
