"""Batch EXIF date extraction via exiftool's -stay_open mode."""

from __future__ import annotations

import subprocess
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional


class ExiftoolProcess:
    """Persistent exiftool process for batch EXIF reads.

    Uses -stay_open True for performance — avoids subprocess overhead per file.
    Pattern from sync_takeout.py.
    """

    def __init__(self) -> None:
        self.proc = subprocess.Popen(
            ["exiftool", "-stay_open", "True", "-@", "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
        )

    def execute(self, args: list) -> str:
        """Send args to exiftool, return output up to {ready} sentinel."""
        cmd = "\n".join(str(a) for a in args) + "\n-execute\n"
        self.proc.stdin.write(cmd)
        self.proc.stdin.flush()
        lines = []
        while True:
            line = self.proc.stdout.readline()
            if not line:
                break
            stripped = line.rstrip("\n")
            if stripped == "{ready}":
                break
            lines.append(stripped)
        return "\n".join(lines)

    def close(self) -> None:
        try:
            self.proc.stdin.write("-stay_open\nFalse\n")
            self.proc.stdin.flush()
            self.proc.wait(timeout=10)
        except Exception:  # pylint: disable=broad-exception-caught
            self.proc.kill()

    def __enter__(self) -> "ExiftoolProcess":
        return self

    def __exit__(self, *_) -> None:
        self.close()


_EXIF_DATE_FMT = "%Y:%m:%d %H:%M:%S"
_VALID_SENTINEL = "-"
_ZERO_DATE = "0000:00:00 00:00:00"


def _parse_exif_date(raw: str) -> Optional[datetime]:
    raw = raw.strip()
    if not raw or raw == _VALID_SENTINEL or raw.startswith(_ZERO_DATE[:4] + ":"):
        return None
    # Strip timezone suffix if present ("2024:06:01 12:00:00+09:00" → drop "+09:00")
    raw = raw[:19]
    try:
        return datetime.strptime(raw, _EXIF_DATE_FMT)
    except ValueError:
        return None


_EXIF_CHUNK = 500  # files per exiftool call — avoids memory/command-line limits


def batch_read_dates(
    paths: list[Path],
    et: ExiftoolProcess,
    chunk_size: int = _EXIF_CHUNK,
) -> dict[Path, Optional[datetime]]:
    """Return {path: DateTimeOriginal} for all paths, chunked to avoid limits.

    Falls back to CreateDate / QuickTime:CreateDate when DateTimeOriginal is absent.
    Processes paths in chunks of chunk_size to keep each exiftool call manageable.
    """
    if not paths:
        return {}

    result: dict[Path, Optional[datetime]] = {}
    for offset in range(0, len(paths), chunk_size):
        chunk = paths[offset: offset + chunk_size]
        result.update(_read_chunk(chunk, et))
    return result


def _read_chunk(paths: list[Path], et: ExiftoolProcess) -> dict[Path, Optional[datetime]]:
    args = ["-DateTimeOriginal", "-CreateDate", "-QuickTime:CreateDate", "-s3", "-f"]
    args += [str(p) for p in paths]
    output = et.execute(args)

    result: dict[Path, Optional[datetime]] = {}
    lines = output.splitlines()
    # exiftool -s3 outputs 3 lines per file (one per tag, in order)
    for i, path in enumerate(paths):
        base = i * 3
        if base + 2 >= len(lines):
            result[path] = None
            continue
        dt_orig = _parse_exif_date(lines[base])
        create = _parse_exif_date(lines[base + 1])
        qt_create = _parse_exif_date(lines[base + 2])
        result[path] = dt_orig or create or qt_create

    return result
