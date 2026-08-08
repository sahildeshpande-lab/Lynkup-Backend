# Data Export (`apps/export`)

Personal data export for authenticated KampuLynk users.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/me/export` | Request an export (requires recent Firebase auth) |
| `GET` | `/api/v1/me/export/{export_id}` | Fetch export status |

There is **no download HTTP endpoint**. The ZIP is emailed as an attachment.

## Flow

1. Authenticated user calls `POST /api/v1/me/export`.
2. A `data_export_requests` row is created with `status=queued`.
3. FastAPI `BackgroundTasks` runs `DataExportService.process_export`.
4. The builder gathers user-owned JSON + media into a ZIP.
5. `LocalExportStorage` stores `exports/<export_id>.zip`.
6. Status becomes `completed`.
7. An email row is inserted into `transactional_email_log` with:
   - HTML body (reference link `{BASE_URL_EXPORT}/{export_id}`)
   - `attachment` = absolute path to the ZIP
   - `is_send=false`
8. The existing email cron (`process_pending_emails`) delivers the message via SendGrid **with the ZIP attached**.
9. After `EXPORT_RETENTION_DAYS`, call `cleanup_expired_exports()` (cron) to delete files and mark `expired`.

## Configuration

```text
EXPORT_STORAGE_PATH=./storage/exports
EXPORT_RETENTION_DAYS=7
BASE_URL_EXPORT=https://lynkup-backend-311u.onrender.com
```

Email reference link format:

```text
{BASE_URL_EXPORT}/{export_id}
```

Example: `https://lynkup-backend-311u.onrender.com/<uuid>`

`BASE_URL_EXPORT` is separate from the general `BASE_URL` used elsewhere (email/auth/images), so Spaces CDN hosts will not accidentally become export links.

## Concurrent exports

Only one `queued` or `processing` export is allowed per user at a time.

## Recent authentication

`POST /me/export` depends on `require_recent_auth` (Firebase `auth_time` within
`RECENT_AUTH_MAX_AGE_SECONDS`). Clients must send a recently issued Firebase ID
token for the export request. The status endpoint uses normal access-token auth.

## Storage abstraction

`ExportStorage` / `LocalExportStorage` isolate archive persistence. S3 or
DigitalOcean Spaces backends can be added later without changing the builder.

## Media limitations

Media is retrieved via the existing Spaces client (`core.images.config.s3_client`)
when configured, then local `entrypoints/static/uploads`, then public HTTP URL
fallback. If media cannot be retrieved, the export still succeeds without that
file.

## Cleanup

```python
from apps.export.cleanup import cleanup_expired_exports

await cleanup_expired_exports()
```

Schedule this via Linux Cron (or similar). It is not started automatically by FastAPI.

## Notes

- Large ZIPs may hit SendGrid attachment size limits.
- The ZIP must remain on disk until the email cron successfully sends it.
