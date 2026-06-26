from __future__ import annotations

from enum import Enum


class UserStatus(str, Enum):
    pending="pending"
    active = "active"
    suspicious_review = "suspicious_review"
    suspended = "suspended"
    banned = "banned"
    
    deleting = "deleting"

class AdminUserStatus(str, Enum):
    active = "active"
    suspended = "suspended"
    banned = "banned"


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



class RegistrationType(str, Enum):
    email = "email"
    google = "google"
    apple = "apple"


class LynkupResponse(str, Enum):
    accepted = "accepted"
    declined = "declined"


class PostState(str, Enum):
    draft = "draft"
    processing = "processing"
    published = "published"
    flagged = "flagged"
    hidden = "hidden"
    deleted = "deleted"


class MediaAssetState(str, Enum):
    draft = "draft"
    processing = "processing"
    published = "published"
    flagged = "flagged"
    hidden = "hidden"
    deleted = "deleted"


class MediaType(str, Enum):
    image = "image"
    video = "video"
    audio = "audio"
    document = "document"
    gif = "gif"
    other = "other"


class ReactionType(str, Enum):
    like = "like"
    love = "love"
    celebrate = "celebrate"
    insightful = "insightful"
    curious = "curious"


from typing import Literal

Role = Literal["user", "moderator", "viewer", "superadmin"]
SocialProvider = Literal["google", "apple"]
