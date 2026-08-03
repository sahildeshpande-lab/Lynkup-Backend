# Milestone 1 Release Note

## Summary

Milestone 1 covers the authentication and session updates delivered in this branch:

- Firebase-native forgot-password email flow
- Logout flow with active installation tracking
- `user_installations.is_active` schema support
- Removal of the account-side reset-password endpoint

## Tables Used

| Table                     | Purpose in Milestone 1                                                          |
| ------------------------- | ------------------------------------------------------------------------------- |
| `users`                   | Local user existence check for forgot-password and authenticated logout context |
| `user_installations`      | Tracks per-device active sessions and logout state via `is_active`              |
| `refresh_tokens`          | Revoked on logout for the current device/session                                |
| `security_events`         | Existing login/security audit trail used by auth flows                          |
| `transactional_email_log` | Existing email logging table used by notification/email workflows               |

## Database Change

The schema change for Milestone 1 is handled in Alembic and mirrored in the SQL companion file:

- Alembic migration: [alembic/versions/9b5c1d7a4f2e_add_is_active_to_user_installations.py](../alembic/versions/9b5c1d7a4f2e_add_is_active_to_user_installations.py)
- SQL summary: [docs/sql/milestone-1-schema-changes.sql](sql/milestone-1-schema-changes.sql)

## Swagger API

Swagger UI:
[https://kampulynk-stage-app-pvj6t.ondigitalocean.app/docs](https://kampulynk-stage-app-pvj6t.ondigitalocean.app/docs)

## Jira Tickets

Ticket links were not present in the repository, so this table is ready for the actual project URLs.

| Ticket | Description                              | Link                                            |
| ------ | ---------------------------------------- | ----------------------------------------------- |
| M1-001 | Backend infrastructure                   | https://kampulynk.atlassian.net/browse/SCRUM-78 |
| M1-002 | Database & API Integration               | https://kampulynk.atlassian.net/browse/SCRUM-76 |
| M1-003 | Logout API and installation tracking     | TBD                                             |
| M1-004 | `user_installations.is_active` migration | TBD                                             |
| M1-005 | Remove account reset-password endpoint   | TBD                                             |

|

## Branch / Repo

Current branch:
[Develop](https://github.com/kampulynk-org/Kampulynk-backend/tree/develop)

Repository:https://github.com/kampulynk-org/Kampulynk-backend/

## Notes

- The account-side reset-password endpoint was removed because Firebase now owns the reset flow.
- The release note reflects the current checked-in branch state in this workspace.
