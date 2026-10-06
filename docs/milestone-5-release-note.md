# Milestone 5 Release Note

## Summary

Milestone 5 covers privacy, account lifecycle, consent, and content moderation delivered in this branch:

- **Account settings** — Change password, notification preferences, profile visibility
- **Account deletion** — User-initiated deletion with a 30-day grace period, then full purge
- **Data export** — Full personal data download (password-protected ZIP emailed to the user)
- **Consent management** — Terms/Privacy opt-in stored in Firestore at registration (and admin user creation)
- **Content moderation** — Configurable blocklist, in-process auto-scan cron, re-scan after edits, user reporting, and admin/moderator queue actions (flag, reject/delete, reinstate, escalate)

## Tables Used

| Table                         | Purpose in Milestone 5                                                                 |
| ----------------------------- | -------------------------------------------------------------------------------------- |
| `users`                       | Authenticated actor; `status`, `deleted_at`, `purge_after` for deletion grace period   |
| `profiles`                    | Profile visibility (`public` / `connections_only` / `private`); export profile payload |
| `notification_preferences`    | Per-user push/in-app toggles and JSONB category preferences                            |
| `notification_types`          | Catalog of notification type codes used by preference categories                       |
| `notification_categories`     | Preference category catalog                                                            |
| `data_export_requests`        | Export job status, Spaces storage key, and download expiry                             |
| `transactional_email_log`     | Queued export-ready email (presigned URL + ZIP password)                               |
| `moderation_words_config`     | Admin-configurable profanity / blocklist (`profanity_words` JSONB)                     |
| `moderation_history`          | Immutable audit log of moderation actions (post/user)                                  |
| `moderation_assignment_state` | Round-robin cursor for assigning posts/reports to moderators                           |
| `reports`                     | User-submitted reports against user, post, or comment                                  |
| `posts`                       | Moderation state, notes, auto-scan timestamp, and words found                          |
| `comments`                    | Auto-scan timestamp and words found; soft-delete on blocklist hit                      |
| `post_revisions`              | Revision snapshot used when a post is reported                                         |
| `admin_configurations`        | Report-count thresholds (post / comment / user) that auto-flag or auto-suspend         |

Firestore (not PostgreSQL):

| Path                                   | Purpose in Milestone 5                                                                     |
| -------------------------------------- | ------------------------------------------------------------------------------------------ |
| `users/{firebase_uid}/consent/current` | Terms and Privacy consent (`terms_accepted`, `privacy_accepted`, `consented_at`, `source`) |

## Database Change

Schema changes for Milestone 5:

**Account deletion**

- Added / used `users.status = deleting`, `users.is_deleted`, `users.deleted_at`, `users.purge_after`
- Permanent purge via PostgreSQL procedure `purge_user_data(p_user_id UUID)` (`database/procedures/purge_user_data.sql`)
- After DB purge, external cleanup removes Firebase Auth user, Stream Chat user, and Spaces objects (profile/banner/media keys)

**Data export**

- Added `data_export_requests` (`queued` / `processing` / `completed` / `failed` / `expired`)
- ZIP stored at `exports/<user_id>/<export_id>.zip` on DigitalOcean Spaces; 7-day retention (`EXPORT_RETENTION_DAYS`)

**Consent**

- Live consent is written to Firestore at `users/{firebase_uid}/consent/current` on email/social signup and admin user creation
- Sources: `signup`, `admin_creation`
- A `consent_records` SQLModel table exists in metadata; current signup/admin-create flows write Firestore, not this table

**Content moderation**

- Added `moderation_words_config` (JSONB `profanity_words`)
- Added `moderation_history` and `moderation_assignment_state`
- Added PostgreSQL enum value `poststate.escalate`
- Added `posts.moderation_notes`, `posts.auto_moderation_scanned_at`, `posts.moderation_words_found`
- Added `comments.auto_moderation_scanned_at`, `comments.moderation_words_found`
- Startup `init_db` applies these columns / enum value with `IF NOT EXISTS` / `ADD VALUE` (programmatic Alembic + `core/database/init.py`)

**Reports / queue**

- `reports` (from Milestone 3) is the user-report intake; Milestone 5 wires report-threshold auto-flag, round-robin moderator assignment, and the admin reported-entity dashboard
- Admin report-count thresholds live in `admin_configurations` (type `threshold`)

## Swagger API

Swagger UI:
[https://kampulynk-stage-app-pvj6t.ondigitalocean.app/docs](https://kampulynk-stage-app-pvj6t.ondigitalocean.app/docs)

### Key endpoints (Milestone 5)

#### Account settings

| Area               | Method  | Path                                |
| ------------------ | ------- | ----------------------------------- |
| Change password    | `POST`  | `/api/v1/auth/change-password`      |
| Get preferences    | `GET`   | `/api/v1/notifications/preferences` |
| Update preferences | `PATCH` | `/api/v1/notifications/preferences` |
| Profile visibility | `PATCH` | `/api/v1/profilevisibility`         |

Use : Change password verifies the Firebase ID token and current password, then updates PostgreSQL and Firebase Auth. Profile visibility accepts `public`, `connections_only`, or `private`.

#### Account deletion

| Area                         | Method   | Path                        |
| ---------------------------- | -------- | --------------------------- |
| Schedule my deletion         | `DELETE` | `/api/v1/users/me/deletion` |
| Admin schedule user deletion | `DELETE` | `/api/v1/users/`            |

**Note:** There is no HTTP trigger for the purge worker. It runs in-process from FastAPI lifespan (`cron_purge_deleted_accounts`) every `ACCOUNT_DELETION_CRON_INTERVAL_HOURS` (default 24).

#### Data export

| Area                 | Method | Path                            |
| -------------------- | ------ | ------------------------------- |
| Request export       | `POST` | `/api/v1/me/export`             |
| Export status        | `GET`  | `/api/v1/me/export/{export_id}` |
| Cleanup expired ZIPs | `POST` | `/api/v1/admin/exports/cleanup` |

Use : Request requires a revoked-checked Firebase ID token (`require_recent_auth`). When generation finishes, the user is emailed a Spaces origin presigned URL and a 6-character ZIP password. There is no backend download endpoint.

**Note:** Expired ZIP cleanup is admin-invoked (`POST /api/v1/admin/exports/cleanup`) until an external scheduler is configured. It is not started in application lifespan.

#### Consent

No dedicated consent HTTP API. Consent is written to Firestore during:

- `POST /api/v1/auth/signup` and social signup (`source=signup`)
- `POST /api/v1/admin/users` (`source=admin_creation`)

#### User reporting

| Area                    | Method  | Path                                |
| ----------------------- | ------- | ----------------------------------- |
| Submit a report         | `POST`  | `/api/v1/reports`                   |
| List reports for entity | `GET`   | `/api/v1/admin/reports`             |
| Reported-entity queue   | `GET`   | `/api/v1/admin/reports/details`     |
| Report by ID            | `GET`   | `/api/v1/admin/reports/{report_id}` |
| Review report           | `PATCH` | `/api/v1/admin/reports`             |

#### Admin moderation queue

| Area                   | Method          | Path                           |
| ---------------------- | --------------- | ------------------------------ |
| Processing queue       | `GET`           | `/api/v1/posts/processing`     |
| Reviewed posts         | `GET`           | `/api/v1/admin/posts/reviewed` |
| Moderate post          | `PATCH`         | `/api/v1/admin/posts/reviewed` |
| Get / update blocklist | `GET` / `POST`  | `/api/v1/moderation-words`     |
| Moderation history     | `GET`           | `/api/v1/status-history`       |
| Report thresholds      | `GET` / `PATCH` | `/api/v1/admin/threshold`      |

Use : `PATCH /api/v1/admin/posts/reviewed` sets `published`, `flagged`, `rejected` (hard delete), `reinstate`, or `escalate` (reassign to a superadmin).

**Note:** Auto-moderation cron is started in FastAPI lifespan (`cron_auto_moderation`). It is not triggered by an admin HTTP route.

## Jira Tickets

Ticket links were not present in the repository, so this table is ready for the actual project URLs.

| Ticket | Description                                                | Link                                             |
| ------ | ---------------------------------------------------------- | ------------------------------------------------ |
| M5-001 | Change password                                            | https://kampulynk.atlassian.net/browse/SCRUM-166 |
| M5-002 | Notification preferences                                   | https://kampulynk.atlassian.net/browse/SCRUM-144 |
| M5-003 | Profile visibility settings                                | https://kampulynk.atlassian.net/browse/SCRUM-167 |
| M5-004 | Account deletion and 30-day data purge                     | https://kampulynk.atlassian.net/browse/SCRUM-153 |
| M5-005 | Full data export and download                              | https://kampulynk.atlassian.net/browse/SCRUM-152 |
| M5-006 | Consent management (Firestore at registration)             | https://kampulynk.atlassian.net/browse/SCRUM-154 |
| M5-007 | Configurable blocklist, auto-scan, and re-scan after edits | https://kampulynk.atlassian.net/browse/SCRUM-37  |
| M5-008 | User reporting and admin moderation queue                  | https://kampulynk.atlassian.net/browse/SCRUM-35  |
| M5-009 | Moderation actions (reject/delete, reinstate, escalate)    | https://kampulynk.atlassian.net/browse/SCRUM-46  |

## Branch / Repo

Current branch:
[Develop](https://github.com/kampulynk-org/Kampulynk-backend)

Repository: https://github.com/kampulynk-org/Kampulynk-backend/

## Notes

### Change password

- `POST /api/v1/auth/change-password` accepts `firebaseId`, `current_password`, and `new_password` (min 8 characters, at least one uppercase letter and one digit).
- Verifies the Firebase ID token, matches the current password against `users.password_hash`, then updates PostgreSQL and Firebase Auth.
- Admin password change remains at `POST /api/v1/change-password` (admin session).

### Notification preferences

- Preferences support global `push_enabled` / `in_app_enabled` plus per-category toggles (`ANNOUNCEMENT`, `TOPIC`, `CONNECTION_REQUEST`, etc.).
- In-app and push are independent: a user with push disabled but in-app enabled still sees notifications in the app.
- Partial `PATCH`: only supplied fields are changed.

### Profile visibility

- `PATCH /api/v1/profilevisibility` sets `profiles.profile_visibility` to `public`, `connections_only`, or `private`.
- Feed, search, and profile views honour visibility (staff roles bypass visibility and block checks).
- Default is `public`.

### Account deletion

- `DELETE /api/v1/users/me/deletion` is idempotent. It sets `status=deleting`, `is_deleted=true`, `deleted_at=now`, and `purge_after=now + ACCOUNT_PURGE_AFTER_DAYS` (default 30).
- Content stays visible during the grace period. Access side effects revoke tokens and deactivate Stream Chat.
- App users who log in during the grace period are restored to `active`. Moderators, viewers, and superadmins cannot recover by logging in.
- After `purge_after`, the lifespan cron calls `purge_user_data` then deletes Firebase Auth, Stream Chat, and Spaces objects.
- Multi-instance purge uses a PostgreSQL advisory lock so only one worker purges at a time.
- Admin `DELETE /api/v1/users/` schedules the same grace-period deletion for selected users.

### Data export

- `POST /api/v1/me/export` enqueues a background job. Duplicate in-progress requests are not started again.
- ZIP is AES-256 password-protected and includes `profile.json`, `posts.json`, `comments.json`, `reactions.json`, `bookmarks.json`, `connections.json`, `notifications.json`, learning JSON, media files, and `README.txt`.
- Email contains the presigned Spaces origin URL (not CDN) and the 6-character ZIP password. The password is not stored.
- Status: `queued` → `processing` → `completed` / `failed`. Completed files expire after `EXPORT_RETENTION_DAYS` (default 7).
- Admin `POST /api/v1/admin/exports/cleanup` deletes expired ZIP objects and marks rows `expired`.

### Consent management

- After a successful PostgreSQL user commit, signup writes Firestore `users/{uid}/consent/current` with `terms_accepted=true`, `privacy_accepted=true`, server timestamp `consented_at`, and `source=signup`.
- Admin-created users use `source=admin_creation`.
- Writes are idempotent (`set(..., merge=True)`). If Firestore fails after the local user exists, the API returns a handled error (`Account created but consent storage failed` / equivalent admin message) rather than rolling back the user.

### Content moderation

- **Configurable blocklist:** `GET` / `POST /api/v1/moderation-words` reads/writes `moderation_words_config.profanity_words` (normalized lowercase unique tokens).
- **Auto-scan cron:** started in FastAPI lifespan when `AUTO_MODERATION_ENABLED=true`. Each cycle scans up to `AUTO_MODERATION_BATCH_SIZE` posts and comments (default 50), then sleeps `AUTO_MODERATION_CRON_INTERVAL_SECONDS` (default 120).
- **Matching:** case-insensitive whole-word match (`spam` hits `spam!` but not `spammer` / `antispam`). This is the bypass / substring rule: keywords are literal (`re.escape`) and bounded by non-word characters.
- **Pre-publish / queue:** new public posts are assigned a moderator (round-robin) and enter the review path (`GET /api/v1/posts/processing` and reviewed lists). Auto-moderation scans caption + HTML body; a blocklist hit sets `PostState.flagged`, records history, and stores `moderation_words_found`. Comments with a hit are soft-deleted.
- **Re-scan after edits:** items with `auto_moderation_scanned_at IS NULL` or `updated_at > auto_moderation_scanned_at` are scanned again. Author edits of a flagged post move it to `processing` (same moderator kept) and bump `updated_at`, so the cron re-scans.
- **No auto-reinstate:** a clean re-scan never moves a flagged post back to published.
- Draft, deleted, and rejected posts are skipped. Auto-moderation does not run a repeated-word / spam-repetition check.

### User reporting and admin queue

- Users report `user`, `post`, or `comment` via `POST /api/v1/reports`. Post reports store the current `post_revision_id`.
- Reports are assigned to a moderator (post’s existing moderator, else round-robin).
- Configurable thresholds (`GET` / `PATCH /api/v1/admin/threshold`) auto-flag a post, soft-delete a comment, or suspend a user when report count is reached.
- Admin dashboard: `GET /api/v1/admin/reports/details` is one row per reported entity (counts, previous moderation comments, search/sort). `PATCH /api/v1/admin/reports` sets `under_review` / `actioned` / `rejected`.

### Moderation actions

- `PATCH /api/v1/admin/posts/reviewed`:
  - `published` — approve
  - `flagged` — hide from public feed
  - `rejected` — hard-delete the post
  - `reinstate` — restore visibility
  - `escalate` — reassign to a superadmin (`PostState.escalate`)
- Actions write `moderation_history` (`GET /api/v1/status-history?entity_id=`).
- `GET /api/v1/admin/posts/reviewed` filters `published`, `flagged`, `rejected`, `reinstate`, `escalate` (`published` includes reinstate; `flagged` includes processing awaiting re-review).

### Environment variables (Milestone 5)

| Area             | Variables                                                                                            |
| ---------------- | ---------------------------------------------------------------------------------------------------- |
| Auto-moderation  | `AUTO_MODERATION_ENABLED`, `AUTO_MODERATION_CRON_INTERVAL_SECONDS`, `AUTO_MODERATION_BATCH_SIZE`     |
| Account deletion | `ACCOUNT_PURGE_AFTER_DAYS`, `ACCOUNT_DELETION_CRON_INTERVAL_HOURS`, `ACCOUNT_DELETION_BATCH_SIZE`    |
| Data export      | `EXPORT_RETENTION_DAYS`, `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_ENDPOINT`                |
| Consent / Auth   | Firebase Admin SDK (`FIREBASE_CREDENTIALS` / `FIREBASE_SERVICE_ACCOUNT_PATH`, `FIREBASE_PROJECT_ID`) |
| Email delivery   | `SENDGRID_API_KEY`, `SENDGRID_FROM_EMAIL`, `SENDGRID_FROM_NAME`                                      |

- The release note reflects the current checked-in branch state in this workspace.
