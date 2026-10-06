from __future__ import annotations

from enum import Enum

from common.auth_messages import ACCOUNT_DOESNT_EXIST_MESSAGE


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
        UserStatus.deleting.value: ACCOUNT_DOESNT_EXIST_MESSAGE,
        UserStatus.pending.value: "Your account is pending",
    }
    return messages.get(status_value, "Your account is not active")


_USER_STATUS_DISPLAY_MAP: dict[str, str] = {
    "pending": "Pending",
    "active": "Active",
    "suspicious_review": "Suspicious_review",
    "suspended": "Suspended",
    "banned": "Banned",
    "deleting": "Deleting",
}


def format_user_status(status: UserStatus | str | None) -> str:
    if status is None:
        return "Pending"
    val = status.value if hasattr(status, "value") else str(status)
    return _USER_STATUS_DISPLAY_MAP.get(val.lower(), val.capitalize())

class AdminUserStatus(str, Enum):
    active = "active"
    suspended = "suspended"
    banned = "banned"


class UserListStatus(str, Enum):
    pending = "Pending"
    active = "Active"
    suspended = "Suspended"
    banned = "Banned"
    deleted = "Deleted"


class StaffListStatus(str, Enum):
    active = "Active"
    deleted = "Deleted"


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

# GET /posts/{id} for regular users (non-staff): these states only.
# Staff (moderator/viewer/superadmin) may fetch flagged, processing, deleted, etc.
# Flagged / processing remain author-only for regular users.
POST_DETAIL_VISIBLE_STATES: tuple[PostState, ...] = (
    PostState.published,
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
    redeemed = "REDEEMED"
    deactivated = "DEACTIVATED"


class AdminInvitationListStatus(str, Enum):
    """Admin list filter labels for GET /admin/invitations."""

    active = "Active"
    redeemed = "Redeemed"
    deleted = "Deleted"
    expired = "Expired"


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
    users = "USERS"


class ReportEntityType(str, Enum):
    user = "user"
    post = "post"
    comment = "comment"


class ReportStatus(str, Enum):
    under_review = "under_review"
    rejected = "rejected"
    actioned = "actioned"


class CatalogAddedBy(str, Enum):
    user = "user"
    admin = "admin"


InterestAddedBy = CatalogAddedBy


class ReportedEntitySort(str, Enum):
    latest_reported_at = "latest_reported_at"
    report_count = "report_count"
    created_at = "created_at"
    updated_at = "updated_at"


class ReportedEntityOrder(str, Enum):
    asc = "asc"
    desc = "desc"


class CatalogSort(str, Enum):
    created_at = "created_at"


class CatalogOrder(str, Enum):
    asc = "asc"
    desc = "desc"



class ReviewedPostSort(str, Enum):
    created_at = "created_at"
    updated_at = "updated_at"
    # Alias of updated_at for older clients.
    latest_post = "latest_post"


class ReviewedPostOrder(str, Enum):
    asc = "asc"
    desc = "desc"


class AdminActivityLogSort(str, Enum):
    created_at = "created_at"
    module = "module"
    action = "action"
    updated_at = "updated_at"


class AdminActivityLogOrder(str, Enum):
    asc = "asc"
    desc = "desc"


class AdminActivityLogRole(str, Enum):
    superadmin = "superadmin"
    moderator = "moderator"


class AdminConfigurationType(str, Enum):
    FEATURE_FLAG = "feature_flag"
    THRESHOLD = "threshold"


class UserActivityLogType(str, Enum):
    SIGN_IN = "SIGN_IN"
    VIEW_POST_LIST = "VIEW_POST_LIST"
    VIEW_POST = "VIEW_POST"
    CREATE_POST = "CREATE_POST"
    CREATE_COMMENT = "CREATE_COMMENT"
    LIKE_POST = "LIKE_POST"
    LIKE_COMMENT = "LIKE_COMMENT"
    REPOST = "REPOST"
    BOOKMARK_POST = "BOOKMARK_POST"
    SHARE_POST = "SHARE_POST"
    SEND_CONNECTION_REQUEST = "SEND_CONNECTION_REQUEST"
    ACCEPT_CONNECTION_REQUEST = "ACCEPT_CONNECTION_REQUEST"
    UPDATE_PROFILE = "UPDATE_PROFILE"


class SpotlightType(str, Enum):
    """Category for the current day in the global 5-day Learning Spotlight cycle."""

    leading_thinker = "leading_thinker"
    country_perspective = "country_perspective"
    influential_research = "influential_research"
    latest_research = "latest_research"
    beyond_your_field = "beyond_your_field"


class SpotlightFeedback(str, Enum):
    """User feedback on a Learning Spotlight paper."""

    useful = "useful"
    not_useful = "not_useful"


class LearningPaperAction(str, Enum):
    """Action recorded in persistent user-paper interaction history."""

    read = "READ"
    save = "SAVE"
    unsave = "UNSAVE"
    like = "LIKE"
    dislike = "DISLIKE"




# Activities that count toward DAU (excludes login-only and passive reads).
DAU_ACTIVITY_TYPES: tuple[UserActivityLogType, ...] = (
    UserActivityLogType.CREATE_POST,
    UserActivityLogType.CREATE_COMMENT,
    UserActivityLogType.LIKE_POST,
    UserActivityLogType.LIKE_COMMENT,
    UserActivityLogType.REPOST,
    UserActivityLogType.BOOKMARK_POST,
    UserActivityLogType.SHARE_POST,
    UserActivityLogType.SEND_CONNECTION_REQUEST,
    UserActivityLogType.ACCEPT_CONNECTION_REQUEST,
    UserActivityLogType.UPDATE_PROFILE,
)


from typing import Literal

Role = Literal["user", "moderator", "viewer", "superadmin"]


class PublicAuthRole(str, Enum):
    """Roles accepted by public signup / social-auth endpoints."""

    user = "user"


SocialProvider = Literal["google", "apple"]
