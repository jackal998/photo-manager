"""Walk source directories and build FileRecord lists with Live Photo pairing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from scanner.media import (
    COMPANION_PHOTO_EXTS,
    EDITED_SUFFIXES,
    MEDIA_EXTENSIONS,
    SKIP_FILENAMES,
    get_file_type,
    parse_media_filename,
)


@dataclass
class FileRecord:
    """A single media file discovered during a source scan."""

    path: Path
    source_label: str        # 'iphone' | 'takeout' | 'jdrive'
    file_type: str           # 'jpeg' | 'heic' | 'raw' | 'png' | 'mp4' | 'mov' | …
    pair_partner: Optional[Path] = None  # MOV partner for Live Photo HEIC, or vice versa
    misnamed: bool = False   # True if magic bytes differ from file extension


def scan_sources(
    sources: dict[str, Path],
    limit: int | None = None,
) -> list[FileRecord]:
    """Walk each source directory and return all discovered FileRecords.

    Args:
        sources: Mapping of label → root path.
        limit: If set, stop after this many files per source (for debug/dry-run).
    """
    records: list[FileRecord] = []
    for label, root in sources.items():
        if not root.exists():
            raise FileNotFoundError(f"Source directory not found: {root}")
        records.extend(_scan_dir(root, label, limit=limit))
    return records


def _scan_dir(root: Path, label: str, limit: int | None = None) -> list[FileRecord]:
    """Recursively walk root and return FileRecords with Live Photo pairs resolved."""
    # Collect all media files grouped by directory for efficient pairing
    by_dir: dict[Path, list[Path]] = {}
    total = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.name.lower() in SKIP_FILENAMES:
            continue
        if path.suffix.lower() not in MEDIA_EXTENSIONS:
            continue
        by_dir.setdefault(path.parent, []).append(path)
        total += 1
        if limit and total >= limit:
            break

    records: list[FileRecord] = []
    for directory, files in by_dir.items():
        records.extend(_process_directory(files, label))
    return records


def _process_directory(files: list[Path], label: str) -> list[FileRecord]:
    """Build FileRecords for one directory, pairing Live Photos by stem."""
    # Build stem → files map using clean stems (strip Takeout numbering + edited suffixes)
    stem_map: dict[str, list[Path]] = {}
    for path in files:
        mf = parse_media_filename(path)
        stem_map.setdefault(mf.clean_stem, []).append(path)

    records: list[FileRecord] = []
    paired: set[Path] = set()

    for path in files:
        if path in paired:
            continue

        file_type, misnamed = get_file_type(path)
        if file_type == "skip":
            continue

        partner = _find_live_photo_partner(path, stem_map)
        if partner is not None:
            paired.add(partner)

        records.append(FileRecord(
            path=path,
            source_label=label,
            file_type=file_type,
            pair_partner=partner,
            misnamed=misnamed,
        ))

    return records


def _find_live_photo_partner(path: Path, stem_map: dict[str, list[Path]]) -> Optional[Path]:
    """Return the paired Live Photo partner for a HEIC/JPG or MOV file, or None.

    HEIC/JPG → look for same-stem MOV
    MOV → look for same-stem HEIC/JPG (using COMPANION_PHOTO_EXTS order)
    Edited copies are excluded from pairing.
    """
    mf = parse_media_filename(path)
    if mf.is_edited:
        return None

    ext = path.suffix.lower()
    candidates = stem_map.get(mf.clean_stem, [])

    if ext in (".heic", ".heif", ".jpg", ".jpeg"):
        # Look for a same-stem MOV
        for candidate in candidates:
            if candidate.suffix.lower() in (".mov", ".mp4") and candidate != path:
                c_mf = parse_media_filename(candidate)
                if not c_mf.is_edited and c_mf.clean_stem == mf.clean_stem:
                    return candidate

    elif ext in (".mov", ".mp4"):
        # Look for a same-stem image (HEIC preferred)
        for photo_ext in COMPANION_PHOTO_EXTS:
            for candidate in candidates:
                if (candidate.suffix == photo_ext and candidate != path):
                    c_mf = parse_media_filename(candidate)
                    if not c_mf.is_edited and c_mf.clean_stem == mf.clean_stem:
                        return candidate

    return None
