from __future__ import annotations

import logging
from typing import Any

from fastapi import UploadFile
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.imports.enums import ImportType
from apps.imports.file_parser import read_import_dataframe
from apps.imports.handlers import get_handler
from common.exceptions import ApiError
from core.cache.catalog import invalidate_catalog_cache

logger = logging.getLogger(__name__)


class ImportService:
    async def import_upload(
        self,
        *,
        import_type: ImportType,
        file: UploadFile,
        db: AsyncSession,
        actor_user_id=None,
        actor_role: str | None = None,
    ) -> dict[str, Any]:
        file_name = (file.filename or "upload").strip() or "upload"
        content = await file.read()
        dataframe = read_import_dataframe(file_name, content)
        handler = get_handler(import_type)
        handler.validate_headers(dataframe)

        try:
            created, duplicates, failures = await handler.process(
                dataframe,
                db,
                actor_user_id=actor_user_id,
                actor_role=str(actor_role) if actor_role is not None else None,
            )
        except ApiError:
            await db.rollback()
            raise
        except (IntegrityError, SQLAlchemyError) as exc:
            await db.rollback()
            logger.exception("Import database error type=%s file=%s", import_type.value, file_name)
            raise ApiError("Import failed due to a database error") from exc

        if created > 0:
            await invalidate_catalog_cache(import_type)

        logger.info(
            "Import completed type=%s file=%s created=%s duplicates=%s failed=%s",
            import_type.value,
            file_name,
            created,
            len(duplicates),
            len(failures),
        )
        return {
            "type": import_type.value,
            "fileName": file_name,
            "totalRecords": created + len(duplicates) + len(failures),
            "successfulCount": created,
            "duplicateCount": len(duplicates),
            "failedCount": len(failures),
            "failedRows": failures,
            "duplicateRows": duplicates,
        }


import_service = ImportService()


async def import_upload(
    *,
    import_type: ImportType,
    file: UploadFile,
    db: AsyncSession,
    actor_user_id=None,
    actor_role: str | None = None,
) -> dict[str, Any]:
    return await import_service.import_upload(
        import_type=import_type,
        file=file,
        db=db,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
    )
