# Data Export (`apps/export`)

Personal data export for authenticated KampuLynk users.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/me/export` | Request an export (requires recent Firebase auth) |
| `GET` | `/api/v1/me/export/{export_id}` | Fetch export status |
| `GET` | `/api/v1/me/export/{export_id}/download` | Auth + redirect to short-lived signed Spaces URL |

## Flow

1. `POST /api/v1/me/export` creates `data_export_requests` with `status=queued`.
2. Background task builds JSON + media into a temporary local ZIP.
3. ZIP is uploaded to DigitalOcean Spaces at:
   ```text
   exports/<user_id>/<export_id>.zip
   ```
4. Temporary local ZIP is deleted.
5. DB updated: `status=completed`, `storage_key` = Spaces object key only.
6. Email queued to `transactional_email_log` with backend download link:
   ```text
   {BASE_URL}/api/v1/me/export/{export_id}/download
   ```
   (ZIP is **not** attached.)
7. Download endpoint authenticates, validates ownership/expiry, then redirects
   to a short-lived presigned Spaces URL.
8. Retention cleanup (external cron) deletes the Spaces object when
   `download_expires_at` passes and sets `status=expired`, `storage_key=NULL`.

## Configuration

Reuses existing Spaces credentials:

```text
S3_BUCKET
S3_ACCESS_KEY
S3_SECRET_KEY
S3_ENDPOINT
```

Export-specific:

```text
EXPORT_RETENTION_DAYS=7
EXPORT_SIGNED_URL_EXPIRES_SECONDS=900
BASE_URL=https://your-backend.example.com
```

Do **not** use `S3_CDN_ENDPOINT` / `S3_FILE_ENDPOINT` for private export downloads.

## Security

- Export objects are uploaded **without** public ACL.
- Signed URLs are short-lived and never stored in PostgreSQL.
- CDN / file endpoint public URLs are not exposed for exports.
- Ownership and expiry are enforced on download.

## Cleanup

```python
from apps.export.cleanup import cleanup_expired_exports

await cleanup_expired_exports()
```

Schedule via Linux Cron. Not auto-started by FastAPI.

## Notes

- Presigned URLs do not reliably signal download completion, so objects are
  retained until retention expiry rather than deleted on redirect.
