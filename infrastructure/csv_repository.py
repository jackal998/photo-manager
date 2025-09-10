from __future__ import annotations

import csv
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator

from loguru import logger

from core.models import PhotoRecord, PhotoGroup
from core.services.interfaces import IPhotoRepository

CSV_HEADERS = [
    "GroupNumber",
    "IsMark",
    "IsLocked",
    "FolderPath",
    "FilePath",
    "Capture Date",
    "Modified Date",
    "FileSize",
]


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except Exception:
        logger.warning("Invalid datetime: {}", value)
        return None


def _parse_bool_int(value: str) -> bool:
    try:
        return str(value).strip() in {"1", "true", "True"}
    except Exception:
        return False


def _ensure_filesize_bytes(file_path: str, file_size_field: str) -> int:
    try:
        # Always overwrite with actual file size per DESIGN.md
        return int(os.path.getsize(file_path))
    except Exception as ex:
        logger.warning("getsize failed for {} ({}), fallback parsing FileSize field", file_path, ex)
        # Fallback try to parse human readable like 1.44MB
        s = str(file_size_field).strip()
        try:
            if s.isdigit():
                return int(s)
            units = {
                "B": 1,
                "KB": 1024,
                "MB": 1024**2,
                "GB": 1024**3,
                "TB": 1024**4,
            }
            num_part = "".join(ch for ch in s if (ch.isdigit() or ch == "."))
            unit_part = "".join(ch for ch in s if ch.isalpha()).upper() or "B"
            factor = units.get(unit_part, 1)
            return int(float(num_part) * factor)
        except Exception:
            return 0


class CsvPhotoRepository(IPhotoRepository):
    def load(self, csv_path: str) -> Iterator[PhotoRecord]:
        path = Path(csv_path)
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            # Validate minimal headers and order (we accept extra columns but ignore)
            missing = [h for h in CSV_HEADERS if h not in reader.fieldnames]
            if missing:
                raise ValueError(f"CSV missing required headers: {missing}")

            for row in reader:
                try:
                    group_number = int(row.get("GroupNumber", "0") or 0)
                    is_mark = _parse_bool_int(row.get("IsMark", "0"))
                    is_locked = _parse_bool_int(row.get("IsLocked", "0"))
                    folder_path = row.get("FolderPath", "") or ""
                    file_path = row.get("FilePath", "") or ""
                    capture_date = _parse_datetime(row.get("Capture Date", ""))
                    modified_date = _parse_datetime(row.get("Modified Date", ""))
                    file_size_bytes = _ensure_filesize_bytes(file_path, row.get("FileSize", "0"))

                    yield PhotoRecord(
                        group_number=group_number,
                        is_mark=is_mark,
                        is_locked=is_locked,
                        folder_path=folder_path,
                        file_path=file_path,
                        capture_date=capture_date,
                        modified_date=modified_date,
                        file_size_bytes=file_size_bytes,
                    )
                except Exception as ex:
                    logger.error("CSV row error: {} | row={} ", ex, row)
                    continue

    def save(self, csv_path: str, groups: Iterable[PhotoGroup]) -> None:
        path = Path(csv_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
            writer.writeheader()
            for group in groups:
                for item in group.items:
                    # Always compute actual size
                    try:
                        size = int(os.path.getsize(item.file_path))
                    except Exception:
                        size = item.file_size_bytes or 0
                    writer.writerow(
                        {
                            "GroupNumber": item.group_number,
                            "IsMark": 1 if item.is_mark else 0,
                            "IsLocked": 1 if item.is_locked else 0,
                            "FolderPath": item.folder_path,
                            "FilePath": item.file_path,
                            "Capture Date": item.capture_date.strftime("%Y-%m-%d %H:%M:%S") if item.capture_date else "",
                            "Modified Date": item.modified_date.strftime("%Y-%m-%d %H:%M:%S") if item.modified_date else "",
                            "FileSize": size,
                        }
                    )
