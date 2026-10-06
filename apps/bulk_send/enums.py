from __future__ import annotations

from enum import Enum

# SendGrid Mail Send API: max personalizations per request.
SENDGRID_MAX_PERSONALIZATIONS = 1000


class EmailCampaignStatus(str, Enum):
    queued = "queued"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class EmailDeliveryStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    sent = "sent"
    failed = "failed"


class BulkEmailTargetType(str, Enum):
    """Audience filter types for bulk email campaigns.

    Values align with push-notification targeting (``NotificationTargetType``)
    using the singular OpenAPI names INTEREST / HASHTAG / USER.
    """

    MAJOR = "MAJOR"
    MINOR = "MINOR"
    EDUCATION_LEVEL = "EDUCATION_LEVEL"
    UNIVERSITY = "UNIVERSITY"
    COUNTRY = "COUNTRY"
    INTEREST = "INTEREST"
    HASHTAG = "HASHTAG"
    USER = "USER"
