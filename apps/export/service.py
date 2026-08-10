from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from html import escape
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from apps.accounts.db_models import User
from apps.export.builder import DataExportBuilder
from apps.export.config import settings as export_settings
from apps.export.enums import DataExportStatus
from apps.export.models import DataExportRequest
from apps.export.password import generate_export_password
from apps.export.schemas import ExportRequestAcceptedData, ExportStatusData
from apps.export.storage import ExportStorage, get_export_storage, write_temp_zip
from apps.profiles.db_models import Profile
from apps.profiles.db_models.university_db_model import University
from common.exceptions import ApiError
from core.database.session import async_session_factory
from core.email_service import BRAND_COLORS, _queue_email, _render_email_layout

logger = logging.getLogger(__name__)

IN_PROGRESS_STATUSES = (DataExportStatus.queued, DataExportStatus.processing)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def build_export_storage_key(*, user_id: UUID, export_id: UUID) -> str:
    return f"exports/{user_id}/{export_id}.zip"


def _format_export_expiry_date(expires: datetime | None) -> str:
    """Return a human-readable expiry date such as ``15 August 2026``."""
    if expires is None:
        return "7 days from generation"
    aware = _ensure_aware(expires)
    assert aware is not None
    return f"{aware.day} {aware.strftime('%B %Y')}"


def _build_export_ready_email_body(
    *,
    download_url: str,
    zip_password: str,
    zip_filename: str,
    download_expires_at: datetime | None,
) -> str:
    """Build the export-ready email body for the shared KampuLynk layout.

    ``download_url`` is used only as the CTA button href — it is never shown
    as visible link text. ``zip_password`` is displayed but never logged.
    """
    text_primary = BRAND_COLORS.get("text_primary", "#071A35")
    text_secondary = BRAND_COLORS.get("text_secondary", "#64748B")
    brand_blue = BRAND_COLORS.get("brand_blue", "#0B5FA5")
    brand_green = BRAND_COLORS.get("brand_green", "#46B12F")
    expiry_text = escape(_format_export_expiry_date(download_expires_at))
    safe_password = escape(zip_password)
    safe_filename = escape(zip_filename)
    # Presigned URLs contain query ampersands; keep them intact in href.
    safe_href = download_url.replace('"', "%22")

    return (
        f'<h1 style="margin:0 0 16px 0;font-size:22px;font-weight:700;color:{text_primary};'
        "letter-spacing:-0.5px;line-height:1.3;text-align:center;"
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;\">"
        "Your data export is ready"
        "</h1>"
        f'<p style="margin:0 0 12px 0;font-size:15px;line-height:1.6;color:{text_secondary};'
        "text-align:center;"
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;\">"
        "Your KampuLynk data export has been generated successfully."
        "</p>"
        f'<p style="margin:0 0 28px 0;font-size:15px;line-height:1.6;color:{text_secondary};'
        "text-align:center;"
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;\">"
        "Your encrypted ZIP archive is ready to download."
        "</p>"
        # Primary CTA — visible label only; raw URL lives solely in href.
        '<table role="presentation" cellspacing="0" cellpadding="0" border="0" align="center" '
        'style="margin:0 auto 32px;width:auto;">'
        "<tr>"
        f'<td align="center" bgcolor="{brand_green}" '
        f'style="border-radius:8px;background-color:{brand_green};">'
        f'<a href="{safe_href}" target="_blank" '
        'style="font-size:15px;'
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;"
        "font-weight:600;color:#ffffff;text-decoration:none;display:inline-block;"
        f'padding:14px 32px;border-radius:8px;border:1px solid {brand_green};">'
        "Download Your Data"
        "</a>"
        "</td>"
        "</tr>"
        "</table>"
        # ZIP password credential box
        f'<p style="margin:0 0 10px 0;font-size:13px;font-weight:600;color:{brand_blue};'
        "letter-spacing:1.5px;text-transform:uppercase;text-align:center;"
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;\">"
        "ZIP Password"
        "</p>"
        '<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" '
        'style="margin:0 0 16px;">'
        "<tr>"
        '<td align="center" style="padding:18px 16px;background-color:#F8FAFC;'
        f'border:2px dashed {brand_blue};border-radius:12px;">'
        f'<span style="font-size:22px;font-weight:700;color:{text_primary};'
        "font-family:'Courier New',Courier,monospace;letter-spacing:4px;\">"
        f"{safe_password}"
        "</span>"
        "</td>"
        "</tr>"
        "</table>"
        f'<p style="margin:0 0 24px 0;font-size:14px;line-height:1.6;color:{text_secondary};'
        "text-align:center;"
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;\">"
        "Your ZIP archive is password protected. Use the password above to open the archive."
        "</p>"
        f'<p style="margin:0 0 8px 0;font-size:13px;line-height:1.5;color:{text_secondary};'
        "text-align:center;"
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;\">"
        f"File: {safe_filename}"
        "</p>"
        f'<p style="margin:0 0 4px 0;font-size:14px;line-height:1.6;color:{text_secondary};'
        "text-align:center;"
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;\">"
        "Download link expires on:"
        "</p>"
        f'<p style="margin:0 0 28px 0;font-size:15px;font-weight:600;line-height:1.5;color:{text_primary};'
        "text-align:center;"
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;\">"
        f"{expiry_text}"
        "</p>"
        f'<p style="margin:0;font-size:13px;line-height:1.6;color:{text_secondary};'
        "text-align:center;"
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;\">"
        "For your security, this email was sent automatically. "
        "If you did not request this export, you can safely ignore this email."
        "</p>"
    )


class DataExportService:
    def __init__(self, storage: ExportStorage | None = None) -> None:
        self.storage = storage or get_export_storage()

    async def request_export(
        self,
        *,
        user: User,
        db: AsyncSession,
    ) -> ExportRequestAcceptedData:
        existing = (
            await db.execute(
                select(DataExportRequest)
                .where(DataExportRequest.user_id == user.id)
                .where(DataExportRequest.status.in_(IN_PROGRESS_STATUSES))
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing:
            raise ApiError(
                "An export request is already in progress. Please wait for it to finish."
            )

        now = utc_now()
        export_request = DataExportRequest(
            user_id=user.id,
            status=DataExportStatus.queued,
            requested_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(export_request)
        await db.commit()
        await db.refresh(export_request)

        return ExportRequestAcceptedData(
            export_id=export_request.id,
            status=export_request.status,
        )

    async def get_export_status(
        self,
        *,
        user: User,
        export_id: UUID,
        db: AsyncSession,
    ) -> ExportStatusData:
        export_request = await self._get_owned_export(user=user, export_id=export_id, db=db)
        return ExportStatusData(
            id=export_request.id,
            status=export_request.status,
            requested_at=export_request.requested_at,
            completed_at=export_request.completed_at,
            download_expires_at=export_request.download_expires_at,
            file_size_bytes=export_request.file_size_bytes,
        )

    async def process_export(self, export_id: UUID) -> None:
        """Background worker entrypoint. Uses its own DB session.

        Steps:
        1. Mark export as processing.
        2. Fetch user Profile + University for password derivation.
        3. Generate deterministic 6-character ZIP password (never logged or stored).
        4. Build AES-256 password-protected ZIP via DataExportBuilder.
        5. Write temporary local ZIP, upload to Spaces, delete temporary file.
        6. Generate 7-day presigned Spaces URL (never stored in DB).
        7. Persist completed status and metadata to DB.
        8. Send email containing presigned URL and ZIP password to user.
        """
        async with async_session_factory() as db:
            export_request = (
                await db.execute(
                    select(DataExportRequest).where(DataExportRequest.id == export_id)
                )
            ).scalar_one_or_none()
            if not export_request:
                logger.error("Export %s not found for processing", export_id)
                return

            if export_request.status not in (
                DataExportStatus.queued,
                DataExportStatus.processing,
            ):
                logger.info(
                    "Skipping export %s with status %s",
                    export_id,
                    export_request.status,
                )
                return

            now = utc_now()
            export_request.status = DataExportStatus.processing
            export_request.started_at = now
            export_request.updated_at = now
            export_request.error_message = None
            db.add(export_request)
            await db.commit()
            await db.refresh(export_request)

            user_id = export_request.user_id
            temp_path = None
            try:
                # Fetch profile and university for password derivation.
                profile = (
                    await db.execute(
                        select(Profile).where(Profile.user_id == user_id)
                    )
                ).scalar_one_or_none()

                university_name: str | None = None
                if profile and profile.university_id:
                    university = (
                        await db.execute(
                            select(University).where(University.id == profile.university_id)
                        )
                    ).scalar_one_or_none()
                    if university:
                        university_name = university.name

                # Generate deterministic 6-character ZIP password.
                # SECURITY: never log, store, or return this value via API.
                zip_password = generate_export_password(
                    first_name=profile.first_name if profile else None,
                    last_name=profile.last_name if profile else None,
                    university=university_name,
                    major=profile.major if profile else None,
                    minor=profile.minor if profile else None,
                )

                builder = DataExportBuilder(db=db, user_id=user_id, export_id=export_id)
                zip_bytes = await builder.build_encrypted_zip_bytes(zip_password)

                # Temporary local ZIP — uploaded then deleted immediately.
                temp_path = write_temp_zip(zip_bytes)
                storage_key = build_export_storage_key(
                    user_id=user_id,
                    export_id=export_id,
                )
                self.storage.upload(storage_key, zip_bytes)

                # Generate 7-day presigned Spaces URL.
                # SECURITY: never log or store this URL; pass only to email.
                _PRESIGNED_URL_TTL_SECONDS = 604800  # 7 days
                presigned_url = self.storage.generate_download_url(
                    storage_key,
                    expires_in=_PRESIGNED_URL_TTL_SECONDS,
                )

                completed_at = utc_now()
                export_request.status = DataExportStatus.completed
                export_request.completed_at = completed_at
                export_request.storage_key = storage_key
                export_request.file_size_bytes = len(zip_bytes)
                export_request.download_expires_at = completed_at + timedelta(
                    days=export_settings.export_retention_days
                )
                export_request.updated_at = completed_at
                export_request.error_message = None
                db.add(export_request)
                await db.commit()
                await db.refresh(export_request)

                await self._queue_ready_email(
                    db=db,
                    export_request=export_request,
                    presigned_url=presigned_url,
                    zip_password=zip_password,
                )
            except Exception as exc:
                logger.exception("Failed processing export %s", export_id)
                failed_at = utc_now()
                export_request.status = DataExportStatus.failed
                export_request.updated_at = failed_at
                export_request.error_message = str(exc)[:2000]
                db.add(export_request)
                await db.commit()
            finally:
                if temp_path is not None:
                    try:
                        temp_path.unlink(missing_ok=True)
                    except Exception:
                        logger.warning(
                            "Failed deleting temporary export ZIP %s",
                            temp_path,
                            exc_info=True,
                        )

    async def cleanup_expired_exports(self, db: AsyncSession) -> int:
        """Delete expired export objects from Spaces and mark records expired."""
        now = utc_now()
        stmt = select(DataExportRequest).where(
            DataExportRequest.status == DataExportStatus.completed,
            DataExportRequest.download_expires_at.is_not(None),
            DataExportRequest.download_expires_at < now,
        )
        exports = (await db.execute(stmt)).scalars().all()
        cleaned = 0
        for export_request in exports:
            if export_request.storage_key:
                try:
                    self.storage.delete(export_request.storage_key)
                except Exception:
                    logger.exception(
                        "Failed deleting Spaces object for export %s",
                        export_request.id,
                    )
            export_request.status = DataExportStatus.expired
            export_request.storage_key = None
            export_request.updated_at = now
            db.add(export_request)
            cleaned += 1
        if cleaned:
            await db.commit()
        return cleaned

    async def _get_owned_export(
        self,
        *,
        user: User,
        export_id: UUID,
        db: AsyncSession,
    ) -> DataExportRequest:
        export_request = (
            await db.execute(
                select(DataExportRequest).where(DataExportRequest.id == export_id)
            )
        ).scalar_one_or_none()
        if not export_request or export_request.user_id != user.id:
            raise ApiError("Export request not found.")
        return export_request

    async def _queue_ready_email(
        self,
        *,
        db: AsyncSession,
        export_request: DataExportRequest,
        presigned_url: str,
        zip_password: str,
    ) -> None:
        """Queue the export-ready email containing the presigned Spaces URL and ZIP password.

        Security:
        - ``presigned_url`` and ``zip_password`` are used only to build the email
          body; they are never logged, stored in the database, or returned via API.
        - The email is the only place these values are sent.
        - The presigned URL appears only as the Download CTA href, never as visible text.
        """
        user = (
            await db.execute(select(User).where(User.id == export_request.user_id))
        ).scalar_one_or_none()
        if not user or not user.email:
            logger.warning(
                "Cannot queue export email; user missing for export %s",
                export_request.id,
            )
            return

        completed = export_request.completed_at or utc_now()
        zip_filename = f"kampulynk_data_export_{completed.strftime('%Y-%m-%d')}.zip"

        body_html = _build_export_ready_email_body(
            download_url=presigned_url,
            zip_password=zip_password,
            zip_filename=zip_filename,
            download_expires_at=export_request.download_expires_at,
        )
        subject = "Your KampuLynk Data Export Is Ready"
        html_content = _render_email_layout(subject, body_html)
        await _queue_email(
            user.email,
            subject,
            html_content,
            purpose="Data Export Ready",
        )
        logger.info(
            "Queued data-export email for export_id=%s",
            export_request.id,
        )


def get_data_export_service() -> DataExportService:
    return DataExportService()
