# Data Export (`apps/export`)

Personal data export for authenticated KampuLynk users.

## Endpoints

| Method | Path                            | Description                                       |
| ------ | ------------------------------- | ------------------------------------------------- |
| `POST` | `/api/v1/me/export`             | Request an export (requires recent Firebase auth) |
| `GET`  | `/api/v1/me/export/{export_id}` | Fetch export status                               |

There is **no** download endpoint. The download link is sent directly in the
export-ready email as a 7-day presigned DigitalOcean Spaces URL.

## Flow

1. `POST /api/v1/me/export` creates `data_export_requests` with `status=queued`.
2. Background task fetches the user's Profile and University name.
3. Generates a deterministic 6-character ZIP password from the user's five
   profile fields (first_name, last_name, university, major, minor) using
   SHA-256 -- **never stored, never logged, never returned via API**.
4. Builds JSON + media into an **AES-256 password-protected ZIP** using
   `pyzipper.AESZipFile` with `WZ_AES` encryption.
5. ZIP is uploaded to DigitalOcean Spaces at:
   ```text
   exports/<user_id>/<export_id>.zip
   ```
6. Temporary local ZIP is deleted immediately after upload.
7. A **7-day presigned Spaces URL** is generated (valid for 604800 seconds).
   It is **never stored in the database**.
8. DB updated: `status=completed`, `storage_key` = Spaces object key only.
9. Email sent to the user containing:
   - The 7-day presigned Spaces URL (direct download link)
   - The 6-character ZIP password
   - Export reference (export_id)
   - Link expiry timestamp
10. Retention cleanup (external cron) deletes the Spaces object when
    `download_expires_at` passes and sets `status=expired`, `storage_key=NULL`.

## Password Generation

```
initials = first_name[0].upper() + last_name[0].upper()
combined = normalize(first_name)|normalize(last_name)|normalize(university)|normalize(major)|normalize(minor)
suffix   = SHA256(combined).hexdigest().upper()[:4]   # [0-9A-F]
password = initials + suffix                           # exactly 6 chars
```

The password is passed only to ZIP encryption and the email. It is not stored
in `data_export_requests` or any other table.

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
```

The 7-day presigned URL TTL (604800 seconds) is hardcoded in service.py.
`BASE_URL_EXPORT` and `EXPORT_SIGNED_URL_EXPIRES_SECONDS` are no longer used
and can be removed from your environment.

## Security

- Export objects are uploaded **without** public ACL (private by default).
- ZIPs are AES-256 password-protected via `pyzipper`.
- Presigned URLs are short-lived (7 days) and never stored in PostgreSQL.
- CDN / file endpoint public URLs are never exposed for exports.
- The ZIP password is never stored, logged, or exposed via API.

## Cleanup

```python
from apps.export.cleanup import cleanup_expired_exports

await cleanup_expired_exports()
```

Schedule via Linux Cron. Not auto-started by FastAPI.

Cleanup flow:
1. Find exports where `status=completed` and `download_expires_at < now`.
2. Delete Spaces object at `storage_key`.
3. Set `status=expired`, `storage_key=NULL`.
4. Keep DB record for audit history.

## Notes

- Presigned URLs becoming invalid does NOT automatically delete Spaces objects.
  The retention cron is responsible for deletion.
- `pyzipper` must be listed in `requirements.txt`.
