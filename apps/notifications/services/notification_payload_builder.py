from __future__ import annotations

import json
from typing import Any
from uuid import UUID


class NotificationPayloadBuilder:
    """
    Single source of truth for notification ``deep_link_payload`` / FCM data shape.

    Add new types by extending ``_DEEP_LINKS`` (and optional field rules) rather than
    duplicating payload construction across services.
    """

    _DEEP_LINKS: dict[str, dict[str, str]] = {
        "CONNECTION_REQUEST": {"screen": "connections", "tab": "requests"},
        "CONNECTION_ACCEPTED": {"screen": "connections", "tab": "connections"},
        "CONNECTION_DECLINED": {"screen": "connections", "tab": "requests"},
        "ANNOUNCEMENT": {"screen": "notifications"},
        "POST_FLAGGED": {"screen": "post"},
        "POST_REINSTATED": {"screen": "post"},
        "POST_REJECTED": {"screen": "post"},
        "ACCOUNT_STATUS_CHANGED": {"screen": "account"},
        # Future examples (uncomment / fill when wiring those types):
        # "DIRECT_MESSAGE": {"screen": "chat"},
        # "TOPIC": {"screen": "notifications"},
        # "POST_LIKE": {"screen": "post"},
        # "COMMENT": {"screen": "post"},
        # "MENTION": {"screen": "post"},
    }

    _SENDER_REQUIRED_TYPES = frozenset(
        {
            "CONNECTION_REQUEST",
            "CONNECTION_ACCEPTED",
            "CONNECTION_DECLINED",
            # "DIRECT_MESSAGE",
            # "POST_LIKE",
            # "COMMENT",
            # "MENTION",
        }
    )

    @classmethod
    def build(
        cls,
        *,
        notification_type: str,
        notification_id: UUID | str | None = None,
        sender_user_id: UUID | str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Build the standardized notification data payload.

        ``extra`` preserves existing type-specific fields (e.g. announcement
        ``broadcast`` / ``campaign_id``) without replacing the standard keys.
        """
        type_name = (notification_type or "").strip().upper()
        payload: dict[str, Any] = {
            "notification_type": type_name,
            "notification_id": str(notification_id) if notification_id else "",
        }

        if sender_user_id is not None or type_name in cls._SENDER_REQUIRED_TYPES:
            payload["sender_user_id"] = (
                str(sender_user_id) if sender_user_id is not None else ""
            )

        if extra:
            for key, value in extra.items():
                if key in {
                    "notification_type",
                    "notification_id",
                    "sender_user_id",
                    "deep_link",
                }:
                    continue
                payload[key] = value

        deep_link = cls._DEEP_LINKS.get(type_name)
        if deep_link is not None:
            payload["deep_link"] = dict(deep_link)
            post_id = payload.get("post_id")
            if post_id and payload["deep_link"].get("screen") == "post":
                payload["deep_link"]["post_id"] = str(post_id)

        return payload

    @classmethod
    def for_fcm(cls, payload: dict[str, Any] | None) -> dict[str, str]:
        """
        Convert a notification data payload into an FCM-safe string map.

        Nested objects/lists (e.g. ``deep_link``) are JSON-serialized.
        """
        if not payload:
            return {}
        result: dict[str, str] = {}
        for key, value in payload.items():
            if value is None:
                result[str(key)] = ""
            elif isinstance(value, (dict, list)):
                result[str(key)] = json.dumps(value, separators=(",", ":"), default=str)
            else:
                result[str(key)] = str(value)
        return result
