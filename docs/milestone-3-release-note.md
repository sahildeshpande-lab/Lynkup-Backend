# Milestone 3 Release Note

## Summary

Milestone 3 covers post engagement features delivered in this branch:

- Like / reactions on posts
- Comments (and comment reactions)
- Repost
- Bookmark
- Report (user, post, or comment)

## Tables Used

| Table               | Purpose in Milestone 3                                                      |
| ------------------- | --------------------------------------------------------------------------- |
| `posts`             | Target entity for engagement; stores like / comment / repost / share counts |
| `post_reactions`    | Per-user post reactions (including like)                                    |
| `comments`          | Post comments and replies                                                   |
| `comment_reactions` | Reactions on comments                                                       |
| `reposts`           | User repost records surfaced in feed                                        |
| `bookmarks`         | Saved / bookmarked posts                                                    |
| `share_events`      | Share tracking related to post engagement                                   |
| `reports`           | User-submitted reports against user, post, or comment                       |
| `profiles`          | Author / actor context on engagement responses                              |
| `users`             | Authenticated actor for engagement and report submission                    |

## Database Change

Schema changes for Milestone 3:

- Added `post_reactions` for likes / reactions (typed reaction, timestamps, composite index)
- Added `like_count` and `repost_count` (and related counters) on `posts`
- Added `comments` and `comment_reactions` tables (including one-based comment levels)
- Added `comment_count` and `share_count` on `posts`
- Added `reposts` table (including `user_id` on repost rows)
- Added `bookmarks` and `share_events` tables
- Added `reports` table for user / post / comment reports

## Swagger API

Swagger UI:
[https://kampulynk-stage-app-pvj6t.ondigitalocean.app/docs](https://kampulynk-stage-app-pvj6t.ondigitalocean.app/docs)

### Key endpoints (Milestone 3)

| Area                        | Method          | Path                                   |
| --------------------------- | --------------- | -------------------------------------- |
| Like / reactions            | `POST`          | `/api/v1/posts/reactions`              |
| Post reaction detail        | `GET`           | `/api/v1/posts/{post_id}/postreaction` |
| Liked posts                 | `GET`           | `/api/v1/posts/liked`                  |
| Create comment              | `POST`          | `/api/v1/posts/comments`               |
| List comments               | `GET`           | `/api/v1/posts/{post_id}/comments`     |
| Delete comment              | `DELETE`        | `/api/v1/comments`                     |
| Comment reactions           | `POST`          | `/api/v1/comments/reactions`           |
| Repost                      | `POST`          | `/api/v1/posts/repost`                 |
| Bookmark / unbookmark       | `PATCH`         | `/api/v1/posts/bookmark`               |
| List bookmarks              | `GET`           | `/api/v1/posts/bookmark`               |
| Report                      | `POST`          | `/api/v1/reports`                      |
| Admin list / update reports | `GET` / `PATCH` | `/api/v1/admin/reports`                |

## Jira Tickets

Ticket links were not present in the repository, so this table is ready for the actual project URLs.

| Ticket | Description           | Link                                                                                                |
| ------ | --------------------- | --------------------------------------------------------------------------------------------------- |
| M3-001 | Post like / reactions | https://kampulynk.atlassian.net/browse/SCRUM-118                                                    |
| M3-002 | Post comments         | https://kampulynk.atlassian.net/browse/SCRUM-17                                                     |
| M3-003 | Repost                | TBD                                                                                                 |
| M3-004 | Bookmark              | https://kampulynk.atlassian.net/browse/SCRUM-119                                                    |
| M3-005 | Search the Post       | https://kampulynk.atlassian.net/browse/SCRUM-124 & https://kampulynk.atlassian.net/browse/SCRUM-125 |

| M3-006 | Invitation | https://kampulynk.atlassian.net/browse/SCRUM-129 & https://kampulynk.atlassian.net/browse/SCRUM-130

## Branch / Repo

Current branch:
[Develop](https://github.com/kampulynk-org/Kampulynk-backend/tree/develop)

Repository:https://github.com/kampulynk-org/Kampulynk-backend/

## Notes

- Reactions support like (and related reaction types) on posts; comment reactions are a separate surface under `/api/v1/comments/reactions`.
- Repost toggle uses `POST /api/v1/posts/repost` with `is_reposted`; reposts also appear in the home feed from Milestone 2.
- Reports accept entity type `user`, `post`, or `comment` via `POST /api/v1/reports`; admins manage them under `/api/v1/admin/reports`.
- Post counter columns (`like_count`, `comment_count`, `repost_count`, `share_count`) are maintained alongside the engagement tables.
- The release note reflects the current checked-in branch state in this workspace.
