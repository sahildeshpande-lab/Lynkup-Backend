from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from apps.accounts.db_models import User
from apps.export.builder import DataExportBuilder
from apps.export.config import settings as export_settings
from apps.export.enums import DataExportStatus
from apps.export.models import DataExportRequest
from apps.export.schemas import ExportRequestAcceptedData, ExportStatusData
from apps.export.storage import ExportStorage, get_export_storage, write_temp_zip
from common.exceptions import ApiError
from core.database.session import async_session_factory
from core.email.config import settings as email_settings
from core.email_service import _queue_email, _render_email_layout

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

    async def get_download_redirect_url(
        self,
        *,
        user: User,
        export_id: UUID,
        db: AsyncSession,
    ) -> str:
        """Validate ownership/status/expiry and return a short-lived signed Spaces URL.

        The object is NOT deleted here. Presigned URLs do not reliably signal
        download completion; retention cleanup deletes objects after expiry.
        """
        export_request = await self._get_owned_export(user=user, export_id=export_id, db=db)

        if export_request.status == DataExportStatus.expired:
            raise ApiError("This export has expired.")
        if export_request.status != DataExportStatus.completed:
            raise ApiError("Export is not ready for download.")

        expires_at = _ensure_aware(export_request.download_expires_at)
        if expires_at is None or expires_at <= utc_now():
            raise ApiError("This export has expired.")

        if not export_request.storage_key:
            raise ApiError("Export file is no longer available.")

        if not self.storage.exists(export_request.storage_key):
            raise ApiError("Export file is no longer available.")

        try:
            return self.storage.generate_download_url(export_request.storage_key)
        except Exception as exc:
            logger.exception("Failed generating signed URL for export %s", export_id)
            raise ApiError("Export file is no longer available.") from exc

    async def process_export(self, export_id: UUID) -> None:
        """Background worker entrypoint. Uses its own DB session."""
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
                builder = DataExportBuilder(db=db, user_id=user_id, export_id=export_id)
                zip_bytes = await builder.build_zip_bytes()

                # Temporary local ZIP only for generation/upload; not final storage.
                temp_path = write_temp_zip(zip_bytes)
                storage_key = build_export_storage_key(
                    user_id=user_id,
                    export_id=export_id,
                )
                self.storage.upload(storage_key, zip_bytes)

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

                await self._queue_ready_email(db=db, export_request=export_request)
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
    ) -> None:
        """Queue export-ready email (download link only) for cron delivery."""
        user = (
            await db.execute(select(User).where(User.id == export_request.user_id))
        ).scalar_one_or_none()
        if not user or not user.email:
            logger.warning(
                "Cannot queue export email; user missing for export %s",
                export_request.id,
            )
            return

        base_url = email_settings.base_url.rstrip("/")
        download_url = f"{base_url}/api/v1/me/export/{export_request.id}/download"
        expires = _ensure_aware(export_request.download_expires_at)
        expiry_text = expires.isoformat() if expires else "the retention period ends"
        completed = export_request.completed_at or utc_now()
        zip_filename = f"kampulynk_data_export_{completed.strftime('%Y-%m-%d')}.zip"

        body_html = (
            '<div style="font-size:16px;font-weight:700;margin-bottom:16px;">'
            "Your KampuLynk data export is ready.</div>"
            '<p style="margin:0 0 14px;">Your data export has been generated successfully.</p>'
            f'<p style="margin:0 0 14px;">The ZIP archive <strong>{zip_filename}</strong> '
            "is ready for download.</p>"
            '<p style="margin:0 0 14px;">Download your export:</p>'
            f'<p style="margin:0 0 14px;"><a href="{download_url}">{download_url}</a></p>'
            f'<p style="margin:0 0 14px;">Export reference: {export_request.id}</p>'
            f'<p style="margin:0;">This export is retained until {expiry_text}.</p>'
        )
        subject = "Your KampuLynk data export is ready"
        html_content = _render_email_layout(subject, body_html)
        await _queue_email(
            user.email,
            subject,
            html_content,
            purpose="Data Export Ready",
        )
        logger.info(
            "Queued data-export email for %s (export_id=%s)",
            user.email,
            export_request.id,
        )


def get_data_export_service() -> DataExportService:
    return DataExportService()
