from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.models import PhotoRecord


@dataclass
class PhotoVM:
    record: PhotoRecord

    @property
    def file_name(self) -> str:
        return Path(self.record.file_path).name

    @property
    def folder_path(self) -> str:
        return self.record.folder_path

    @property
    def size_bytes(self) -> int:
        return int(self.record.file_size_bytes or 0)

    @property
    def is_mark(self) -> bool:
        return bool(self.record.is_mark)

    @property
    def is_locked(self) -> bool:
        return bool(self.record.is_locked)
