"""Lightweight view model wrapper around `PhotoRecord`."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.models import PhotoRecord


@dataclass
class PhotoVM:
    """Expose convenient properties for bindings/templates."""

    record: PhotoRecord

    @property
    def file_name(self) -> str:
        """Base name of the file path."""
        return Path(self.record.file_path).name

    @property
    def folder_path(self) -> str:
        """Folder portion of the file path."""
        return self.record.folder_path

    @property
    def size_bytes(self) -> int:
        """File size in bytes (fallback to 0 when missing)."""
        return int(self.record.file_size_bytes or 0)

    @property
    def is_mark(self) -> bool:
        """True if the record is marked."""
        return bool(self.record.is_mark)

    @property
    def is_locked(self) -> bool:
        """True if the record is locked."""
        return bool(self.record.is_locked)
