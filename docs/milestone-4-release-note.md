# Milestone 4 Release Note

## Summary

Milestone 4 covers real-time chat, notifications, and learning recommendations delivered in this branch:

- **Chat** — Stream Chat token issuance and user sync (no local message storage)
- **Notifications** — In-app inbox, user preferences, FCM push, admin ANNOUNCEMENT/TOPIC campaigns, Firebase topic subscriptions
- **Recommendations** — Profile/post keyword extraction, Semantic Scholar paper generation, admin-tunable cron settings, stored paper feed API

## Tables Used

| Table                                   | Purpose in Milestone 4                                                             |
| --------------------------------------- | ---------------------------------------------------------------------------------- |
| `users`                                 | Authenticated actor for chat tokens, notifications, and recommendations            |
| `profiles`                              | Stream user display context; keyword and recommendation snapshot storage           |
| `user_installations`                    | Active device FCM tokens for push delivery                                         |
| `notification_types`                    | Catalog of notification type codes (CONNECTION_REQUEST, ANNOUNCEMENT, TOPIC, etc.) |
| `notification_categories`               | Preference category catalog (drives default category preferences)                  |
| `notification_preferences`              | Per-user push/in-app toggles and JSONB category preferences                        |
| `notification_campaigns`                | Admin ANNOUNCEMENT and TOPIC campaigns                                             |
| `notification_campaign_audience`        | Resolved recipient audit log per campaign                                          |
| `notifications`                         | In-app notification rows (personal + one broadcast row per campaign)               |
| `connection_requests` / `connections`   | Triggers connection-related personal notifications                                 |
| `posts`                                 | Post-level extracted keywords feeding profile keyword aggregation                  |
| `learning_recommendation_settings`      | Admin cron configuration (enable, frequency, max papers)                           |
| `learning_recommendation_settings_logs` | Admin settings change history                                                      |
| `learning_recommendation_logs`          | Archived recommendation snapshots per user                                         |

## Database Change

Schema changes for Milestone 4:

**Notifications**

- Added `notification_types`, `notification_preferences`, `notification_campaigns`, `notification_campaign_audience`, and `notifications`
- Added PostgreSQL enums `notificationcampaigntype` and `notificationcampaignstatus`
- Seeded notification types: `CONNECTION_REQUEST`, `CONNECTION_ACCEPTED`, `CONNECTION_DECLINED`, `DIRECT_MESSAGE`, `ANNOUNCEMENT`, `TOPIC`
- Added `user_installations.fcm_token` for Firebase Cloud Messaging device tokens

**Recommendations**

- Added `profiles.extracted_keywords`, `profiles.keywords_updated_at`
- Added `posts.extracted_keywords`, `posts.keywords_updated_at`
- Added `profiles.learning_recommendations`, `profiles.recommendations_updated_at`
- Added `learning_recommendation_logs` for archived snapshots
- Added `learning_recommendation_settings` with default seed row
- Migrations: `30286e6acc34_add_extracted_keywords_to_profiles_and_.py`, `b7f6d1a2c3e4_learning_recommendation_snapshot_storage_v1.py`, `c2a4d9f0b1c2_learning_recommendation_settings_v1.py`

**Chat**

- No local database tables — Stream Chat holds message and channel data externally

## Swagger API

Swagger UI:
[https://kampulynk-stage-app-pvj6t.ondigitalocean.app/docs](https://kampulynk-stage-app-pvj6t.ondigitalocean.app/docs)

### Key endpoints (Milestone 4)

#### Chat

| Area              | Method | Path                 |
| ----------------- | ------ | -------------------- |
| Stream Chat token | `POST` | `/api/v1/chat/token` |

Use : To create an stream token

#### Notifications (user)

| Area               | Method  | Path                                           |
| ------------------ | ------- | ---------------------------------------------- |
| Get preferences    | `GET`   | `/api/v1/notifications/preferences`            |
| Update preferences | `PATCH` | `/api/v1/notifications/preferences`            |
| List notifications | `GET`   | `/api/v1/notifications`                        |
| Mark all read      | `PATCH` | `/api/v1/notifications/read-all`               |
| Mark one read      | `PATCH` | `/api/v1/notifications/{notification_id}/read` |

#### Notifications (admin)

| Area                       | Method   | Path                          |
| -------------------------- | -------- | ----------------------------- |
| List campaigns             | `GET`    | `/api/v1/admin/notifications` |
| Create + dispatch campaign | `POST`   | `/api/v1/admin/notifications` |
| Update campaign            | `PATCH`  | `/api/v1/admin/notifications` |
| Delete campaign            | `DELETE` | `/api/v1/admin/notifications` |

#### Recommendations

| Area                  | Method  | Path                                    |
| --------------------- | ------- | --------------------------------------- |
| Get stored papers     | `GET`   | `/api/v1/recommendations/papers`        |
| Get admin settings    | `GET`   | `/api/v1/admin/recommendation-settings` |
| Update admin settings | `PATCH` | `/api/v1/admin/recommendation-settings` |
| Manual cron trigger   | `POST`  | `/api/v1/admin/runcron`                 |

**Note : Currently cron runs while by triggering /api/v1/admin/runcron Only Admin Side**

## Jira Tickets

Ticket links were not present in the repository, so this table is ready for the actual project URLs.

| Ticket | Description                                | Link                                             |
| ------ | ------------------------------------------ | ------------------------------------------------ | --- |
| M4-001 | Stream Chat token and user sync            | https://kampulynk.atlassian.net/browse/SCRUM-136 |
| M4-002 | Notification preferences and in-app inbox  | https://kampulynk.atlassian.net/browse/SCRUM-144 |
| M4-003 | FCM push and connection notifications      | https://kampulynk.atlassian.net/browse/SCRUM-140 |
| M4-004 | Admin ANNOUNCEMENT / TOPIC campaigns       | https://kampulynk.atlassian.net/browse/SCRUM-141 |     |
| M4-005 | Keyword extraction and engagement scoring  | https://kampulynk.atlassian.net/browse/SCRUM-40  |
| M4-006 | Semantic Scholar recommendation generation | https://kampulynk.atlassian.net/browse/SCRUM-40  |
|  |

## Branch / Repo

Current branch:
[Develop](https://github.com/kampulynk-org/Kampulynk-backend)

Repository: https://github.com/kampulynk-org/Kampulynk-backend/

## Notes

### Chat

- Chat uses **Stream Chat**; the backend mints tokens and upserts user identity (id, name, profile photo).
- Requires `STREAM_API_KEY` and `STREAM_SECRET_KEY` in environment configuration.
- Stream users are synced on login and profile update; tokens are revoked on logout (best-effort).
- Inactive, suspended, banned, or deleting accounts cannot obtain a chat token.

### Notifications

- **Preferences** support global `push_enabled` / `in_app_enabled` plus per-category toggles (`ANNOUNCEMENT`, `TOPIC`, `CONNECTION_REQUEST`, etc.).
- **In-app** and **push** are independent: a user with push disabled but in-app enabled still sees notifications in the app.
- **Personal notifications** (e.g. connection request/accept/decline) respect preferences in `create_notification`.
- **Campaign push** (ANNOUNCEMENT and TOPIC) filters recipients by `push_enabled` and category preference before sending FCM — opted-out users do not receive device push but may still see broadcast rows in-app when enabled.
- **Inbox** merges personal notifications with admin broadcast campaigns; TOPIC broadcasts are visible only when the user matches stored Firebase topics in the campaign payload.
- **Broadcast read state** is shared: broadcast rows always appear as unread in the API; mark-read acknowledges without mutating the global row.
- **Admin campaigns:** ANNOUNCEMENT targets all active users; TOPIC targets segments (university, major, minor, education level, country, interests, hashtags). One broadcast notification row is created per campaign, not one row per recipient.
- **Firebase topics** are managed server-side from profile attributes (university, major, interests, hashtags, etc.); the mobile app must not subscribe/unsubscribe topics directly.
- **Connection flows** send `CONNECTION_REQUEST`, `CONNECTION_ACCEPTED`, and `CONNECTION_DECLINED` notifications automatically.
- `DIRECT_MESSAGE` is seeded as a notification type for future chat notification wiring.

### Recommendations

- **User API** reads stored papers from `profiles.learning_recommendations` only — no live Semantic Scholar call on GET.
- **Keyword extraction** uses spaCy and KeyBERT from posts and profiles; engagement actions (like, bookmark, comment, repost) contribute weighted keyword scores.
- **Cron generation** builds a Boolean Semantic Scholar query from top profile keywords and persists results on the profile.
- **Cron eligibility:** runs only when admin settings have `is_enabled=true`; skips users inside the configured frequency window or when keywords have not changed since the last run.
- **Admin settings** defaults: enabled, 14-day frequency, max 10 papers (configurable via PATCH).
- **Manual cron** is available at `POST /api/v1/admin/runcron` for development and testing; a background scheduler is not wired in application lifespan yet.
- Requires `SEMANTIC_SCHOLAR_API_KEY` (optional) and `SEMANTIC_SCHOLAR_BASE_URL` for paper generation.

### Environment variables (Milestone 4)

| Area            | Variables                                                                    |
| --------------- | ---------------------------------------------------------------------------- |
| Chat            | `STREAM_API_KEY`, `STREAM_SECRET_KEY`                                        |
| Notifications   | Firebase Admin SDK configuration (FCM + topic subscribe/unsubscribe)         |
| Recommendations | `SEMANTIC_SCHOLAR_API_KEY`, `SEMANTIC_SCHOLAR_BASE_URL`, `MIN_KEYWORD_SCORE` |

- The release note reflects the current checked-in branch state in this workspace.
