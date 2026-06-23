# Milestone 1 Release Note

## Summary
Milestone 1 covers the authentication and session updates delivered in this branch:

- Firebase-native forgot-password email flow
- Logout flow with active installation tracking
- `user_installations.is_active` schema support
- Removal of the account-side reset-password endpoint

## Tables Used

| Table | Purpose in Milestone 1 |
| --- | --- |
| `users` | Local user existence check for forgot-password and authenticated logout context |
| `user_installations` | Tracks per-device active sessions and logout state via `is_active` |
| `refresh_tokens` | Revoked on logout for the current device/session |
| `security_events` | Existing login/security audit trail used by auth flows |
| `transactional_email_log` | Existing email logging table used by notification/email workflows |

## Database Change

The schema change for Milestone 1 is handled in Alembic and mirrored in the SQL companion file:

- Alembic migration: [alembic/versions/9b5c1d7a4f2e_add_is_active_to_user_installations.py](../alembic/versions/9b5c1d7a4f2e_add_is_active_to_user_installations.py)
- SQL summary: [docs/sql/milestone-1-schema-changes.sql](sql/milestone-1-schema-changes.sql)

## Swagger API

Swagger UI:
[http://localhost:8000/docs](http://localhost:8000/docs)

OpenAPI JSON:
[http://localhost:8000/openapi.json](http://localhost:8000/openapi.json)

## Jira Tickets

Ticket links were not present in the repository, so this table is ready for the actual project URLs.

| Ticket | Description | Link |
| --- | --- | --- |
| M1-001 | Firebase forgot-password email flow | TBD |
| M1-002 | Logout API and installation tracking | TBD |
| M1-003 | `user_installations.is_active` migration | TBD |
| M1-004 | Remove account reset-password endpoint | TBD |

## Branch / Repo

Current branch:
[main](https://github.com/sahildeshpande-lab/Lynkup-Backend/tree/main)

Repository:
[https://github.com/sahildeshpande-lab/Lynkup-Backend](https://github.com/sahildeshpande-lab/Lynkup-Backend)

## Notes

- The account-side reset-password endpoint was removed because Firebase now owns the reset flow.
- The release note reflects the current checked-in branch state in this workspace.
