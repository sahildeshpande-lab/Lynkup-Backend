from __future__ import annotations

from enum import Enum


class UserStatus(str, Enum):
    pending="pending"
    active = "active"
    suspicious_review = "suspicious_review"
    suspended = "suspended"
    banned = "banned"
    
    deleting = "deleting"


def inactive_account_message(status: UserStatus | str | None) -> str:
    status_value = status.value if hasattr(status, "value") else str(status or "").lower()
    messages = {
        UserStatus.banned.value: "Your account is banned ",
        UserStatus.suspended.value: "Your account is suspended",
        UserStatus.deleting.value: "Account doesn't exist ",
        UserStatus.pending.value: "Your account is pending",
    }
    return messages.get(status_value, "Your account is not active")

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
    rejected = "rejected"
    reinstate = "reinstate"
    escalate = "escalate"
    hidden = "hidden"
    deleted = "deleted"


# User-facing surfaces (feed, profile post list, search) show these states.
# ``reinstate`` stays its own status — it is visible, not remapped to published.
FEED_VISIBLE_POST_STATES: tuple[PostState, ...] = (
    PostState.published,
    PostState.reinstate,
)

# Owner profile count / own post list: include flagged + processing so authors
# still see moderated / re-submitted posts. Visitors use the cached public count
# (published + reinstate only) and never see processing.
OWNER_VISIBLE_POST_STATES: tuple[PostState, ...] = (
    PostState.published,
    PostState.flagged,
    PostState.processing,
    PostState.reinstate,
)


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
    celebrate = "celebrate"
    insightful = "insightful"
    support = "support"
    curious = "curious"


class InvitationStatus(str, Enum):
    active = "ACTIVE"
    expired = "EXPIRED"
    deactivated = "DEACTIVATED"


class NotificationCampaignType(str, Enum):
    announcement = "ANNOUNCEMENT"
    topic = "TOPIC"


class NotificationCampaignStatus(str, Enum):
    draft = "DRAFT"
    scheduled = "SCHEDULED"
    sent = "SENT"
    failed = "FAILED"


class NotificationTargetType(str, Enum):
    university = "UNIVERSITY"
    major = "MAJOR"
    minor = "MINOR"
    education_level = "EDUCATION_LEVEL"
    country = "COUNTRY"
    interests = "INTERESTS"
    hashtags = "HASHTAGS"


class ReportEntityType(str, Enum):
    user = "user"
    post = "post"
    comment = "comment"


class ReportStatus(str, Enum):
    under_review = "under_review"
    rejected = "rejected"
    actioned = "actioned"


class AdminConfigurationType(str, Enum):
    FEATURE_FLAG = "feature_flag"
    THRESHOLD = "threshold"


from typing import Literal

Role = Literal["user", "moderator", "viewer", "superadmin"]
SocialProvider = Literal["google", "apple"]
