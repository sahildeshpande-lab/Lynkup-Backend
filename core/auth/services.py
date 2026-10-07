from __future__ import annotations

import json
import logging
from typing import Any

from firebase_admin import auth, exceptions, messaging

from .firebase_app import initialize_firebase_app

logger = logging.getLogger(__name__)

_INVALID_TOKEN_ERROR_TYPES = (
    messaging.UnregisteredError,
    messaging.SenderIdMismatchError,
    exceptions.InvalidArgumentError,
)


def verify_firebase_token(token: str, check_revoked: bool = False):
    initialize_firebase_app()
    decoded_token = auth.verify_id_token(token, check_revoked=check_revoked)

    return decoded_token


def revoke_firebase_tokens(uid: str):
    """
    Revokes all refresh tokens for a given Firebase user UID.
    This should be called when an account is suspended, banned, deleted, or password reset.
    """
    initialize_firebase_app()
    auth.revoke_refresh_tokens(uid)


def create_firebase_custom_token(uid: str) -> str:
    """Mint a custom token so the calling client can refresh its Firebase session."""
    initialize_firebase_app()
    token = auth.create_custom_token(uid)
    if isinstance(token, bytes):
        return token.decode("utf-8")
    return str(token)


def disable_firebase_user(uid:str):
    initialize_firebase_app()
    auth.update_user(uid,disabled=True)
    auth.revoke_refresh_tokens(uid)

def enable_firebase_user(uid:str):
    initialize_firebase_app()
    auth.update_user(uid,disabled=False)

def update_firebase_password(uid:str,password:str):
    initialize_firebase_app()
    auth.update_user(uid,password=password)


def update_firebase_email(uid: str, email: str) -> None:
    """Update the email on an existing Firebase user; UID remains unchanged."""
    initialize_firebase_app()
    auth.update_user(uid, email=email, email_verified=False)


_SOCIAL_PROVIDER_IDS = frozenset({"google.com", "apple.com"})


def unlink_social_login_providers(uid: str) -> list[str]:
    """Unlink Google/Apple when the Firebase user still has a password provider.

    Social-only accounts are left linked so email change cannot lock them out.
    """
    initialize_firebase_app()
    record = auth.get_user(uid)
    providers = list(record.provider_data or [])
    has_password = any(getattr(provider, "provider_id", None) == "password" for provider in providers)
    social_ids: list[str] = []
    seen: set[str] = set()
    for provider in providers:
        provider_id = getattr(provider, "provider_id", None)
        if provider_id in _SOCIAL_PROVIDER_IDS and provider_id not in seen:
            seen.add(provider_id)
            social_ids.append(provider_id)
    if not has_password or not social_ids:
        return []
    auth.update_user(uid, providers_to_delete=social_ids)
    return social_ids


def create_firebase_user(email: str, password: str, display_name: str | None = None):
    initialize_firebase_app()
    return auth.create_user(
        email=email,
        password=password,
        display_name=display_name,
        email_verified=True,
    )


def delete_firebase_user(uid: str):
    delete_firebase_user_safely(uid)


def delete_firebase_user_safely(firebase_uid: str | None) -> None:
    """Delete a Firebase Auth user, ignoring missing users and logging other failures."""
    if not firebase_uid:
        return

    initialize_firebase_app()
    try:
        auth.delete_user(firebase_uid)
    except auth.UserNotFoundError:
        pass
    except Exception:
        logger.exception(
            "Failed to delete Firebase user during signup cleanup. uid=%s",
            firebase_uid,
        )


def _stringify_fcm_data(data: dict[str, Any] | None) -> dict[str, str] | None:
    """FCM data payloads require string keys and values.

    Nested dict/list values (e.g. deep_link) are JSON-serialized.
    """
    if data is None:
        return None

    result: dict[str, str] = {}
    for key, value in data.items():
        if value is None:
            result[str(key)] = ""
        elif isinstance(value, (dict, list)):
            result[str(key)] = json.dumps(value, separators=(",", ":"), default=str)
        else:
            result[str(key)] = str(value)
    return result


def _is_invalid_fcm_token_error(exc: BaseException) -> bool:
    if isinstance(exc, _INVALID_TOKEN_ERROR_TYPES):
        return True
    message = str(exc).lower()
    return any(
        token in message
        for token in (
            "unregistered",
            "invalidregistration",
            "invalid-registration-token",
            "registration-token-not-registered",
            "senderidmismatch",
            "mismatched-credential",
        )
    )


def send_push_notification(
    fcm_token: str,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
    *,
    badge: int | None = None,
) -> str:
    """
    Send a single FCM push notification using the existing Firebase Admin app.

    Returns the Firebase message id on success.
    Invalid-token errors are logged and re-raised so callers can record the token
    without treating it as a fatal process failure.

    When ``badge`` is provided (including ``0``), it is set as Android
    ``notification_count`` and APNs ``aps.badge`` so launcher/icon badges match
    the backend unread count for the target user.
    """
    initialize_firebase_app()

    token = (fcm_token or "").strip()
    if not token:
        raise ValueError("fcm_token cannot be blank")

    android_notification_kwargs: dict[str, Any] = {
        "channel_id": "kampulynk_alerts_v3",
        "sound": "default",
    }
    aps_kwargs: dict[str, Any] = {"sound": "default"}
    if badge is not None:
        android_notification_kwargs["notification_count"] = int(badge)
        aps_kwargs["badge"] = int(badge)

    message = messaging.Message(
        token=token,
        notification=messaging.Notification(title=title, body=body),
        data=_stringify_fcm_data(data),
        android=messaging.AndroidConfig(
            priority="high",
            notification=messaging.AndroidNotification(**android_notification_kwargs),
        ),
        apns=messaging.APNSConfig(
            headers={"apns-priority": "10"},
            payload=messaging.APNSPayload(aps=messaging.Aps(**aps_kwargs)),
        ),
    )

    try:
        message_id = messaging.send(message)
        logger.info("FCM message sent successfully: %s", message_id)
        return message_id
    except Exception as exc:
        if _is_invalid_fcm_token_error(exc):
            logger.warning(
                "FCM invalid/unregistered token while sending push: token=%s error=%s",
                token,
                exc,
            )
        else:
            logger.exception(
                "FCM push send failed: token=%s error=%s",
                token,
                exc,
            )
        raise


def send_push_notifications(
    fcm_tokens: list[str],
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
    *,
    badge: int | None = None,
) -> dict[str, Any]:
    """
    Send the same FCM push to many tokens.

    Continues after individual failures. Returns counts and failed tokens so
    NotificationService can deactivate installations later.

    ``badge`` is optional and applied identically to every token (same user's
    devices share one backend unread count).
    """
    successful_count = 0
    failed_count = 0
    failed_tokens: list[str] = []

    for raw_token in fcm_tokens or []:
        token = (raw_token or "").strip()
        if not token:
            continue

        try:
            send_push_notification(token, title, body, data, badge=badge)
            successful_count += 1
        except Exception as exc:
            failed_count += 1
            failed_tokens.append(token)
            if not _is_invalid_fcm_token_error(exc):
                logger.error(
                    "FCM batch send continuing after failure for token=%s: %s",
                    token,
                    exc,
                )

    return {
        "successful_count": successful_count,
        "failed_count": failed_count,
        "failed_tokens": failed_tokens,
    }


def send_push_to_topic(
    topic: str,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
) -> str:
    """
    Publish one FCM notification to a Firebase topic.

    Returns the Firebase message id on success.

    Unused in production (token fan-out is used instead). Before enabling for
    production delivery, implement per-user badge / unread_count semantics —
    topic publish cannot safely carry one unread count for every subscriber.
    """
    initialize_firebase_app()

    topic_name = (topic or "").strip()
    if not topic_name:
        raise ValueError("topic cannot be blank")

    message = messaging.Message(
        topic=topic_name,
        notification=messaging.Notification(title=title, body=body),
        data=_stringify_fcm_data(data),
        android=messaging.AndroidConfig(priority="high",notification=messaging.AndroidNotification(channel_id="kampulynk_alerts_v3",sound="default",
                                        ),),
        apns=messaging.APNSConfig(payload=messaging.APNSPayload(aps=messaging.Aps(sound="default")))
    )

    try:
        print(f"[FCM TOPIC SEND] topic={topic_name} title={title!r} body={body!r}")
        message_id = messaging.send(message)
        print(f"[FCM TOPIC SEND OK] topic={topic_name} message_id={message_id}")
        logger.info("FCM topic message sent successfully: topic=%s id=%s", topic_name, message_id)
        return message_id
    except Exception as exc:
        print(f"[FCM TOPIC SEND ERROR] topic={topic_name} error={exc}")
        logger.exception("FCM topic push failed: topic=%s", topic_name)
        raise


def send_push_to_topics(
    topics: list[str] | set[str],
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Publish the same FCM notification to many Firebase topics.

    Continues after individual topic failures.
    """
    successful_count = 0
    failed_count = 0
    failed_topics: list[str] = []

    for raw_topic in topics or []:
        topic = (raw_topic or "").strip()
        if not topic:
            continue
        try:
            send_push_to_topic(topic, title, body, data)
            successful_count += 1
        except Exception:
            failed_count += 1
            failed_topics.append(topic)
            logger.exception("FCM topic batch continuing after failure for topic=%s", topic)

    return {
        "successful_count": successful_count,
        "failed_count": failed_count,
        "failed_topics": failed_topics,
    }
