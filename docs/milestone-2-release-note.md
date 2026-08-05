# Milestone 2 Release Note

## Summary

Milestone 2 covers user profile management, connections (Lynkup), posts/feed, and admin user administration delivered in this branch:

- Edit profile and delete account (user side)
- Connections: accept, decline, and remove (user side)
- Posts: list posts, feed, draft, and publish (user side)
- Admin: delete user and create user
- Admin: update user profile

## Tables Used

| Table                        | Purpose in Milestone 2                                                                |
| ---------------------------- | ------------------------------------------------------------------------------------- |
| `users`                      | Account identity, soft-delete / deleting status, admin create and delete              |
| `profiles`                   | User and admin profile fields (name, bio, university, major, minor, visibility, etc.) |
| `profile_stats`              | Follower / following / connection-related counters updated by Lynkup flows            |
| `academic_interests`         | Profile academic interest references on edit                                          |
| `universities`               | University lookup used by profile update                                              |
| `education_levels`           | Education level lookup used by profile update                                         |
| `connection_requests`        | Pending Lynkup requests; accept / decline outcomes                                    |
| `connections`                | Accepted Lynkup relationships; remove connection                                      |
| `follows`                    | Follow graph adjustments tied to connection lifecycle                                 |
| `user_roles` / `roles`       | Role assignment when admin creates users (user / moderator / viewer)                  |
| `posts`                      | User posts (draft / published), list, and feed source                                 |
| `post_revisions`             | Revision history for post create / edit                                               |
| `post_attachments`           | Media attached to posts                                                               |
| `media_assets`               | Uploaded media metadata for posts                                                     |
| `hashtags` / `post_hashtags` | Hashtag sync on post create / edit                                                    |

## Database Change

Schema changes for Milestone 2:

- Extended `profiles` / user profile columns for bio, university, major, minor, education level, visibility, and related fields
- Added `profile_stats` and profile count columns used by Lynkup / follow flows
- Restructured academic interests (including integer interest IDs on profiles)
- Consolidated post content into a JSONB `content` field (caption / HTML)
- Updated `media_assets` columns for post media uploads
- Soft-delete / `deleting` status support on `users` for account deletion
- Connection tables (`connection_requests`, `connections`, `follows`) for accept, decline, and remove

## Swagger API

Swagger UI:
[https://kampulynk-stage-app-pvj6t.ondigitalocean.app/docs](https://kampulynk-stage-app-pvj6t.ondigitalocean.app/docs)

### Key endpoints (Milestone 2)

| Area                      | Method   | Path                                    |
| ------------------------- | -------- | --------------------------------------- |
| Edit profile (user)       | `PATCH`  | `/api/v1/updateprofile`                 |
| Delete account (user)     | `DELETE` | `/api/v1/users/me/deletion`             |
| Accept / decline Lynkup   | `POST`   | `/api/v1/lynkupresponse`                |
| Remove connection         | `DELETE` | `/api/v1/lynkupremove`                  |
| List posts                | `GET`    | `/api/v1/posts`                         |
| Feed                      | `GET`    | `/api/v1/feed`                          |
| Create draft / publish    | `POST`   | `/api/v1/post` (`is_draft`)             |
| List drafts               | `GET`    | `/api/v1/draftpost`                     |
| Delete draft              | `DELETE` | `/api/v1/draftpost`                     |
| Admin delete user         | `DELETE` | `/api/v1/users/`                        |
| Admin create user         | `POST`   | `/api/v1/admin/users`                   |
| Admin update user profile | `PATCH`  | `/api/v1/updateuserprofile?id={userId}` |

## Jira Tickets

Ticket links were not present in the repository, so this table is ready for the actual project URLs.

| Ticket | Description                                 | Link |
| ------ | ------------------------------------------- | ---- |
| M2-001 | Edit profile (user side)                    | TBD  |
| M2-002 | Delete account (user side)                  | TBD  |
| M2-003 | Connections: accept / decline Lynkup        | TBD  |
| M2-004 | Connections: remove Lynkup                  | TBD  |
| M2-005 | Posts: list posts, feed, draft, and publish | TBD  |
| M2-006 | Admin: delete user                          | TBD  |
| M2-007 | Admin: create user                          | TBD  |
| M2-008 | Admin: update user profile                  | TBD  |

## Branch / Repo

Current branch:
[Develop](https://github.com/kampulynk-org/Kampulynk-backend/tree/develop)

Repository:https://github.com/kampulynk-org/Kampulynk-backend/

## Notes

- User publish is driven by `POST /api/v1/post` with `is_draft=false` (there is no separate public publish-only HTTP route).
- User self-delete schedules soft-delete / `deleting` status rather than hard-deleting rows immediately.
- Admin create user provisions Firebase + local `users` (and profile for app users) with role assignment via `user_roles`.
- Admin `PATCH /api/v1/updateuserprofile` updates another user’s profile by `id`; admin’s own profile update uses `PATCH /api/v1/update-profile`.
- The release note reflects the current checked-in branch state in this workspace.
