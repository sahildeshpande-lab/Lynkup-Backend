from __future__ import annotations

from apps.accounts.db_models import ConsentRecord, SecurityEvent, RefreshToken, User, UserInstallation, TransactionalEmailLog, Role, Permission, UserRole, RolePermission
from apps.administration.db_models import (
    AdminActivityLog,
    AdminConfiguration,
    AdminSession,
    AdminSigningKey,
    Template,
)

from apps.analytics.db_models import UserActivityLog
from apps.profiles.db_models import (
    AcademicInterest,
    Country,
    EducationLevel,
    Major,
    Minor,
    Profile,
    University,
)
from apps.connections.db_models import ConnectionRequest, Connection, Follow, Block, ConnectionRecommendationSnapshot
from apps.feed.db_models import Post, PostRevision, MediaAsset, PostAttachment, Hashtag, PostHashtag, Topic, PostTopic, LinkPreview
from apps.engagement.db_models import PostReaction, Repost, Bookmark, ShareEvent, Comment, CommentReaction
from apps.moderation.db_models import ModerationWordsConfig, ModerationAssignmentState, ModerationHistory, ModerationHistory
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
from apps.export.models import DataExportRequest
from apps.bulk_send.models import EmailCampaign, EmailDelivery
from apps.learningspotlight.db_models import (
    LearningContent,
    LearningPaperInteraction,
    LearningSpotlightDailyRun,
)

__all__ = [
    "AdminActivityLog",
    "AdminConfiguration",
    "AdminSession",
    "AdminSigningKey",
    "Template",
    "ConsentRecord",

    "Country",
    "Profile",
    "AcademicInterest",
    "EducationLevel",
    "Major",
    "Minor",
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
    "DataExportRequest",
    "EmailCampaign",
    "EmailDelivery",
    "LearningContent",
    "LearningPaperInteraction",
    "LearningSpotlightDailyRun",
    "UserActivityLog",
]


