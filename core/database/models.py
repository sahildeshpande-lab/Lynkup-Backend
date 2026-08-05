from __future__ import annotations

from apps.accounts.db_models import ConsentRecord, SecurityEvent, RefreshToken, User, UserInstallation, TransactionalEmailLog, Role, Permission, UserRole, RolePermission
from apps.profiles.db_models import Country, Profile, AcademicInterest, EducationLevel, University, ProfileStats
from apps.connections.db_models import ConnectionRequest, Connection, Follow, Block, ConnectionRecommendationSnapshot
from apps.feed.db_models import Post, PostRevision, MediaAsset, PostAttachment, Hashtag, PostHashtag, Topic, PostTopic, LinkPreview
from apps.engagement.db_models import PostReaction, Repost, Bookmark, ShareEvent, Comment, CommentReaction
from apps.moderation.db_models import ModerationWordsConfig, ModerationAssignmentState, ModerationHistory
from apps.invitations.db_models import Invitation
from apps.report.db_models import Report
from apps.notifications.db_models import (
    Notification,
    NotificationCampaign,
    NotificationCampaignAudience,
    NotificationCategory,
    NotificationPreference,
    NotificationType,
)

__all__ = [
    "ConsentRecord",
    "Country",
    "Profile",
    "ProfileStats",
    "AcademicInterest",
    "EducationLevel",
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
    "ConnectionRequest",
    "Connection",
    "Follow",
    "Block",
    "ConnectionRecommendationSnapshot",
    "Post",
    "PostRevision",
    "MediaAsset",
    "PostAttachment",
    "Hashtag",
    "PostHashtag",
    "Topic",
    "PostTopic",
    "LinkPreview",
    "PostReaction",
    "Repost",
    "Bookmark",
    "ShareEvent",
    "Comment",
    "CommentReaction",
    "Report",
    "ModerationWordsConfig",
    "ModerationAssignmentState",
    "ModerationHistory",
    "Invitation",
    "NotificationType",
    "NotificationCategory",
    "NotificationPreference",
    "NotificationCampaign",
    "NotificationCampaignAudience",
    "Notification",
]

