# Milestone 6 Release Note

## Summary

Milestone 6 delivers the full **Admin Portal** suite and the **Bulk Email Communication System**:

- **Admin Authentication & RBAC** — Dedicated admin credentials, JWT session cookies, CSRF protection, and role-based access control (`superadmin`, `moderator`, `viewer`).
- **Admin User & Role Management** — User list with filtering/sorting, user creation, profile edits, role assignments, account suspension/activation, and scheduled deletions.
- **Content Moderation & Review Queues** — Pre-publish moderation processing queue, reviewed posts management (`published`, `flagged`, `rejected`, `reinstate`, `escalate`), custom profanity/blocklist configuration, automated scanning, user report intake, and configurable report thresholds.
- **Admin Activity Logs** — Immutable, auditable activity logs recording all administrative actions across modules (`user`, `post`, `report`, `threshold`, `feature_flag`, `bulk_send`, `recommendation_settings`) with before/after state diffs.
- **Feature Flags & Platform Controls** — Admin toggleable feature flags (`recommendation`, etc.) and system configuration management.
- **Admin Notifications** — In-app administrative alert feed, unread counters, and mark-as-read workflows.
- **Bulk Email Communication System** — Targeted bulk email campaign creation, audience filtering (university, country, education level, major/minor, academic interests, account status, registration date, or CSV/XLSX recipient upload), DigitalOcean Spaces attachment handling, batch delivery queue, and SendGrid delivery tracking.

---

## Tables Used

| Table                              | Purpose in Milestone 6                                                                                                                                |
| ---------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `users`                            | Admin and client user records; authentication, credentials (`password_hash`), role (`role`), and status (`active`, `suspended`, `banned`, `deleting`) |
| `profiles`                         | User profile data; academic information, university/country linkages, and profile completeness                                                        |
| `admin_activity_logs`              | Immutable audit log of all admin/staff mutations with module, action, role, description, and JSONB metadata diffs                                     |
| `admin_configurations`             | Platform configurations, dynamic feature flags (`configuration_type = 'feature_flag'`), and report thresholds                                         |
| `email_campaigns`                  | Bulk email campaigns; subject, HTML/text body, audience filters, attachments JSONB, status, and recipient counts                                      |
| `email_deliveries`                 | Per-recipient delivery tracking for bulk campaigns; status (`pending`, `sent`, `failed`), retry attempts, and SendGrid message IDs                    |
| `notifications`                    | In-app notification records for users and administrators                                                                                              |
| `notification_preferences`         | User notification preferences, push/in-app toggles, and category configurations                                                                       |
| `moderation_words_config`          | Configurable profanity and blocklist keywords (`profanity_words` JSONB)                                                                               |
| `moderation_history`               | Historical timeline of moderation actions taken on posts, comments, or users                                                                          |
| `moderation_assignment_state`      | Round-robin moderator assignment state and cursor tracking                                                                                            |
| `reports`                          | User-submitted reports against posts, comments, or profiles                                                                                           |
| `posts`                            | Post entity; moderation state (`PostState`), assignment, notes, and auto-scan metadata                                                                |
| `comments`                         | Comment entity; auto-moderation scan status and soft-delete enforcement on blocklist hits                                                             |
| `learning_recommendation_settings` | Platform recommendation and spotlight settings, synchronized with the `recommendation` feature flag                                                   |

---

## Database Changes

Schema additions and updates delivered in Milestone 6:

**Bulk Email & Campaigns**

- Added `email_campaigns` table with fields `name`, `subject`, `body_html`, `body_text`, `status` (`draft`, `queued`, `processing`, `completed`, `failed`, `cancelled`), `total_recipients`, `attachments` (JSONB), `started_at`, `completed_at`.
- Added `email_deliveries` table with unique constraint `(campaign_id, user_id)`, indexing on `(campaign_id, status)`, retry tracking (`attempt_count`, `last_attempt_at`), `failure_reason`, and `sendgrid_message_id`.

**Admin Activity Logs & Auditing**

- Added `admin_activity_logs` table indexing `module`, `action`, `user_id`, `created_at` with JSONB `metadata` supporting structured change diffs (`old` vs `new`).

**Feature Flags & System Configurations**

- Added `admin_configurations` table with enum `AdminConfigurationType` (`feature_flag`, `threshold`, `system`), unique key constraint, and JSONB `value`.

**Admin Notifications & Moderation State**

- Extended `notifications` and `admin_notifications` queries with filtering on admin roles.
- Enhanced `PostState` handling with `escalate` transitions and superadmin reassignment.

---

## Swagger API

Swagger UI Documentation:
[https://kampulynk-stage-app-pvj6t.ondigitalocean.app/docs](https://kampulynk-stage-app-pvj6t.ondigitalocean.app/docs)

### Key Endpoints (Milestone 6)

#### 1. Admin Authentication & Session Management

| Area                  | Method | Path                       | Description                                                                                    |
| --------------------- | ------ | -------------------------- | ---------------------------------------------------------------------------------------------- |
| Admin Login           | `POST` | `/api/v1/auth/admin/login` | Authenticate admin user with email/password; issues secure session token and HTTP-only cookies |
| Admin Token Refresh   | `POST` | `/api/v1/auth/admin/token` | Refresh active admin authentication session                                                    |
| Admin Profile         | `GET`  | `/api/v1/me`               | Fetch current authenticated admin user profile, role, and permissions                          |
| Admin Password Change | `POST` | `/api/v1/change-password`  | Update authenticated admin password with complexity validation                                 |
| Forgot Password       | `POST` | `/api/v1/forgot-password`  | Request password reset token via email                                                         |
| Reset Password        | `POST` | `/api/v1/reset-password`   | Set new password using verified reset token                                                    |

#### 2. Admin User & Role Management

| Area                   | Method   | Path                            | Description                                                                   |
| ---------------------- | -------- | ------------------------------- | ----------------------------------------------------------------------------- |
| List Users             | `GET`    | `/api/v1/users`                 | List platform users with search, role filters, status filters, and pagination |
| Create Admin User      | `POST`   | `/api/v1/admin/users`           | Provision a new staff user (`moderator`, `viewer`, `superadmin`)              |
| User Details           | `GET`    | `/api/v1/users/{userId}`        | Retrieve full profile and account details for a specific user                 |
| Update User Status     | `PATCH`  | `/api/v1/users/{userId}/status` | Activate, suspend, or ban user accounts                                       |
| Update User Role       | `PATCH`  | `/api/v1/users/{userId}/role`   | Assign or update user roles                                                   |
| Admin Update Profile   | `PATCH`  | `/api/v1/updateuserprofile`     | Administrative update of user profile data and catalog affiliations           |
| Schedule User Deletion | `DELETE` | `/api/v1/users/`                | Administratively schedule account deletion with grace-period policy           |
| List Moderators        | `GET`    | `/api/v1/moderators`            | List active platform moderators                                               |
| List Viewers           | `GET`    | `/api/v1/viewers`               | List active platform viewer accounts                                          |

#### 3. Content Moderation & Reporting Queue

| Area               | Method  | Path                                | Description                                                                          |
| ------------------ | ------- | ----------------------------------- | ------------------------------------------------------------------------------------ |
| Processing Queue   | `GET`   | `/api/v1/posts/processing`          | Queue of pending posts assigned to the moderator                                     |
| Reviewed Posts     | `GET`   | `/api/v1/admin/posts/reviewed`      | Filter reviewed posts by `published`, `flagged`, `rejected`, `reinstate`, `escalate` |
| Moderate Post      | `PATCH` | `/api/v1/admin/posts/reviewed`      | Take moderation action: publish, flag, reject (delete), reinstate, or escalate       |
| Blocklist Words    | `GET`   | `/api/v1/moderation-words`          | Fetch active profanity and sensitive keyword blocklist                               |
| Update Blocklist   | `POST`  | `/api/v1/moderation-words`          | Add or update custom profanity/blocklist keywords                                    |
| Moderation History | `GET`   | `/api/v1/status-history`            | Audit trail of moderation decisions for a post or user                               |
| User Reports Queue | `GET`   | `/api/v1/admin/reports/details`     | Aggregated reported entities queue with counts and history                           |
| List Reports       | `GET`   | `/api/v1/admin/reports`             | List incoming user reports with filters                                              |
| Report Detail      | `GET`   | `/api/v1/admin/reports/{report_id}` | Retrieve specific user report details and context snapshot                           |
| Review Report      | `PATCH` | `/api/v1/admin/reports`             | Update report status (`under_review`, `actioned`, `rejected`)                        |
| Report Thresholds  | `GET`   | `/api/v1/admin/threshold`           | View automated flagging and suspension report thresholds                             |
| Update Thresholds  | `PATCH` | `/api/v1/admin/threshold`           | Update report count thresholds for posts, comments, and users                        |

#### 4. Bulk Email & Communication System

| Area                 | Method | Path                                              | Description                                                               |
| -------------------- | ------ | ------------------------------------------------- | ------------------------------------------------------------------------- |
| Upload Attachment    | `POST` | `/api/v1/admin/bulk-send/attachments`             | Upload attachments/documents to DigitalOcean Spaces for email campaigns   |
| Create Bulk Campaign | `POST` | `/api/v1/admin/bulk-send/campaigns`               | Create and queue a targeted bulk email campaign                           |
| List Campaigns       | `GET`  | `/api/v1/admin/bulk-send/campaigns`               | List bulk email campaigns with search, status filters, and pagination     |
| Campaign Detail      | `GET`  | `/api/v1/admin/bulk-send/campaigns/{campaign_id}` | Detailed metrics: total recipients, sent count, delivered count, failures |

#### 5. Admin Activity Logs & Feature Flags

| Area                | Method   | Path                                    | Description                                               |
| ------------------- | -------- | --------------------------------------- | --------------------------------------------------------- |
| List Activity Logs  | `GET`    | `/api/v1/admin/activity-logs`           | Filterable, paginated audit logs across all admin actions |
| List Logged Modules | `GET`    | `/api/v1/admin/activity-logs/modules`   | List distinct modules logged for filter dropdowns         |
| List Feature Flags  | `GET`    | `/api/v1/feature-flags`                 | List all platform feature flags with their enabled status |
| Update Feature Flag | `PATCH`  | `/api/v1/admin/feature-flags`           | Toggle feature flags (e.g. `recommendation` system)       |
| Create Feature Flag | `POST`   | `/api/v1/admin/feature-flags`           | Create new custom platform feature flag                   |
| Delete Feature Flag | `DELETE` | `/api/v1/admin/feature-flags/{flag_id}` | Remove custom feature flag                                |

#### 6. Admin Notifications

| Area                   | Method  | Path                                    | Description                                                 |
| ---------------------- | ------- | --------------------------------------- | ----------------------------------------------------------- |
| List Notifications     | `GET`   | `/api/v1/admin/notifications`           | Fetch paginated administrative notifications with filtering |
| Mark Notification Read | `PATCH` | `/api/v1/admin/notifications/{id}/read` | Mark specific admin notification as read                    |
| Mark All Read          | `PATCH` | `/api/v1/admin/notifications/read-all`  | Mark all admin notifications as read                        |

---

## Jira Tickets

| Ticket | Description                                                   | Link                                             |
| ------ | ------------------------------------------------------------- | ------------------------------------------------ |
| M6-001 | Admin Authentication, Session Cookies, and RBAC               | https://kampulynk.atlassian.net/browse/SCRUM-158 |
| M6-002 | Content Moderation Queues, Blocklist, and Escalations         | https://kampulynk.atlassian.net/browse/SCRUM-155 |
| M6-003 | User Reporting Aggregated Dashboard and Threshold Enforcement | https://kampulynk.atlassian.net/browse/SCRUM-62  |
| M6-004 | Immutable Admin Activity Logging with Structured Diffs        | https://kampulynk.atlassian.net/browse/SCRUM-68  |

---

## Branch / Repo

Current branch:
[Develop](https://github.com/kampulynk-org/Kampulynk-backend)

Repository: https://github.com/kampulynk-org/Kampulynk-backend/

---

## Implementation Notes

### Admin Portal & RBAC

- **Roles:** Three administrative roles are supported: `superadmin` (full platform control), `moderator` (content moderation, report actioning, user reviews), and `viewer` (read-only audit access).
- **Session Security:** Admin sessions utilize short-lived JWT tokens accompanied by secure, HttpOnly, SameSite cookies. Sensitive administrative actions require elevated roles and generate immutable audit logs.
- **Password Policies:** Enforces strong passwords (minimum 8 characters, requiring mixed-case letters and numbers) with Argon2/PBKDF2 hashing.

### Content Moderation Workflow

- **Pre-Publish Review:** Posts created by standard users enter the `processing` state and are deterministically assigned to a moderator via a round-robin cursor (`moderation_assignment_state`).
- **Automated Scanning:** Background cron scans newly submitted and edited content against `moderation_words_config`. Exact word-boundary matches automatically flag posts and soft-delete comments.
- **Moderator Actions:** Moderators can approve (`published`), flag (`flagged`), reject/delete (`rejected`), restore (`reinstate`), or escalate (`escalate`) items to superadmins.
- **Reporting & Thresholds:** User reports against posts, comments, or users accumulate in the queue. When configured report thresholds (`admin_configurations`) are met, automated actions (such as hiding content or suspending accounts) trigger automatically.

### Bulk Email Communication System

- **Audience Segmentation:** Administrators can target users by academic criteria (university, country, education level, major, minor, interests), account status (`active`, `suspended`), registration dates, or via direct CSV/XLSX recipient uploads.
- **Attachments:** Files uploaded via `POST /api/v1/admin/bulk-send/attachments` are stored securely on DigitalOcean Spaces and linked as campaign attachments.
- **Asynchronous Delivery Engine:** Campaigns are queued in `email_campaigns` and processed in batches by the background delivery worker. It handles SendGrid API rate limits, logs message IDs in `email_deliveries`, tracks delivery statuses, and captures failure reasons without blocking HTTP workers.

### Admin Activity Logging

- All state-altering administrative actions across users, posts, moderation rules, thresholds, feature flags, bulk campaigns, and recommendation settings write to `admin_activity_logs`.
- Logs record the actor's user ID, role, target module, action type, and JSONB metadata containing `old` and `new` property snapshots for comprehensive auditability.

### Feature Flags & Recommendation Sync

- Feature flags managed via `/api/v1/admin/feature-flags` control platform features dynamically.
- Two-way synchronization ensures toggling the `"recommendation"` feature flag automatically updates `learning_recommendation_settings.is_enabled` (and vice-versa), keeping the Learning Spotlight and Recommendation engine in sync with platform configuration.

---

## Environment Variables (Milestone 6)

| Area                    | Variables                                                                                        | Description                                               |
| ----------------------- | ------------------------------------------------------------------------------------------------ | --------------------------------------------------------- |
| Admin Authentication    | `JWT_SECRET_KEY`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `REFRESH_TOKEN_EXPIRE_DAYS`                     | JWT signing secret and expiration lifetimes               |
| Bulk Email & SendGrid   | `SENDGRID_API_KEY`, `SENDGRID_FROM_EMAIL`, `SENDGRID_FROM_NAME`, `BULK_SEND_BATCH_SIZE`          | SendGrid integration and batching configuration           |
| Object Storage (Spaces) | `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_ENDPOINT`, `S3_REGION`                        | DigitalOcean Spaces credentials for attachments & exports |
| Moderation Engine       | `AUTO_MODERATION_ENABLED`, `AUTO_MODERATION_CRON_INTERVAL_SECONDS`, `AUTO_MODERATION_BATCH_SIZE` | Content auto-moderation worker settings                   |
| Background Dispatchers  | `LEARNING_SPOTLIGHT_CRON_INTERVAL_HOURS`, `RECOMMENDATION_CRON_INTERVAL_HOURS`                   | Background cron intervals                                 |
