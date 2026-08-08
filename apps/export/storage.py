from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO

from apps.export.config import settings as export_settings

logger = logging.getLogger(__name__)


class ExportStorage(ABC):
    """Abstract storage for generated export ZIP archives.

    Implementations such as S3ExportStorage / DigitalOceanSpacesExportStorage
    can be added later without changing DataExportService.
    """

    @abstractmethod
    def save(self, storage_key: str, data: bytes) -> str:
        """Persist bytes and return the storage_key used."""

    @abstractmethod
    def exists(self, storage_key: str) -> bool:
        ...

    @abstractmethod
    def open(self, storage_key: str) -> BinaryIO:
        """Open the archive for streaming download."""

    @abstractmethod
    def delete(self, storage_key: str) -> None:
        ...

    @abstractmethod
    def absolute_path(self, storage_key: str) -> Path | None:
        """Optional filesystem path for local storage; None for remote backends."""


class LocalExportStorage(ExportStorage):
    """Store export ZIPs under a configurable local directory."""

    def __init__(self, base_path: str | Path | None = None) -> None:
        self.base_path = Path(base_path or export_settings.export_storage_path).resolve()
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _resolve(self, storage_key: str) -> Path:
        # storage_key is relative (e.g. exports/<uuid>.zip); never trust absolute paths
        clean = storage_key.replace("\\", "/").lstrip("/")
        if ".." in clean.split("/"):
            raise ValueError("Invalid storage key")
        path = (self.base_path / clean).resolve()
        if not str(path).startswith(str(self.base_path)):
            raise ValueError("Invalid storage key")
        return path

    def save(self, storage_key: str, data: bytes) -> str:
        path = self._resolve(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        logger.info("Saved export archive to %s (%d bytes)", path, len(data))
        return storage_key

    def exists(self, storage_key: str) -> bool:
        try:
            return self._resolve(storage_key).is_file()
        except ValueError:
            return False

    def open(self, storage_key: str) -> BinaryIO:
        return self._resolve(storage_key).open("rb")

    def delete(self, storage_key: str) -> None:
        try:
            path = self._resolve(storage_key)
        except ValueError:
            return
        if path.is_file():
            path.unlink()
            logger.info("Deleted export archive %s", path)

    def absolute_path(self, storage_key: str) -> Path | None:
        try:
            return self._resolve(storage_key)
        except ValueError:
            return None


def get_export_storage() -> ExportStorage:
    """Factory for the active export storage backend (local for now)."""
    return LocalExportStorage()
