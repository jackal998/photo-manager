"""Walk source directories and build FileRecord lists with Live Photo pairing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from loguru import logger

from scanner.media import (
    COMPANION_PHOTO_EXTS,
    EDITED_SUFFIXES,
    MEDIA_EXTENSIONS,
    SKIP_FILENAMES,
    get_file_type,
    parse_media_filename,
)


def _has_win32_unsafe_name(name: str) -> bool:
    """True if a filename ends in '.' or whitespace.

    NTFS preserves these characters but the Win32 GUI layer (Explorer, file
    dialogs, most third-party tools) silently strips them on display. Worse,
    pathlib's ``is_dir`` / ``exists`` / ``rglob`` recurse FAIL on such paths
    unless they are accessed via the ``\\\\?\\`` NT-prefix raw API — so any
    files INSIDE a trailing-dot folder are silently invisible to a normal
    pathlib walk. See photo-manager#169.
    """
    return bool(name) and (name[-1] == "." or name[-1].isspace())


@dataclass
class FileRecord:
    """A single media file discovered during a source scan."""

    path: Path
    source_label: str        # user-supplied label (e.g. folder name or custom key)
    file_type: str           # 'jpeg' | 'heic' | 'raw' | 'png' | 'mp4' | 'mov' | …
    pair_partner: Optional[Path] = None  # MOV partner for Live Photo HEIC, or vice versa
    misnamed: bool = False   # True if magic bytes differ from file extension


def scan_sources(
    sources: dict[str, Path],
    limit: int | None = None,
    recursive_map: dict[str, bool] | None = None,
) -> list[FileRecord]:
    """Walk each source directory and return all discovered FileRecords.

    Args:
        sources: Mapping of label → root path.
        limit: If set, stop after this many files per source (for debug/dry-run).
        recursive_map: Optional per-label recursive flag.  ``True`` (or absent)
            means walk all subdirectories; ``False`` means top-level files only.
            When ``None`` all sources are scanned recursively (original behaviour).
    """
    records: list[FileRecord] = []
    for label, root in sources.items():
        if not root.exists():
            raise FileNotFoundError(f"Source directory not found: {root}")
        recursive = True if recursive_map is None else recursive_map.get(label, True)
        records.extend(_scan_dir(root, label, limit=limit, recursive=recursive))
    return records


def _traverses_symlink(path: Path, root: Path) -> bool:
    """Return True if path or any directory between path and root is a symlink/junction.

    Without this guard, the scanner would pull files reached via symlinks or
    Windows junction points into the manifest, and the recycle-bin step would
    later route them out of the configured source root via send2trash.
    """
    current = path
    while current != root and current != current.parent:
        if current.is_symlink():
            return True
        current = current.parent
    return False


def _scan_dir(
    root: Path,
    label: str,
    limit: int | None = None,
    recursive: bool = True,
) -> list[FileRecord]:
    """Walk root and return FileRecords with Live Photo pairs resolved.

    Args:
        root: Root directory to scan.
        label: Source label assigned to every returned record.
        limit: Stop after this many files (for debug/dry-run).
        recursive: When ``True`` walk all subdirectories (default); when
            ``False`` scan only the immediate files in ``root``.
    """
    # Collect all media files grouped by directory for efficient pairing.
    # Keys are str(parent), not Path. On Windows pathlib equality is
    # case-INSENSITIVE — two genuinely-distinct sibling directories that
    # differ only by case (rare but possible on case-sensitive NTFS dirs)
    # would collapse and lose one. See photo-manager#170.
    by_dir: dict[str, list[Path]] = {}
    total = 0
    warned_unsafe: set[str] = set()
    glob_fn = root.rglob if recursive else root.glob
    for path in glob_fn("*"):
        # photo-manager#169: warn ONCE per trailing-dot/whitespace name.
        # rglob enumerates such paths but pathlib operations on them fail —
        # any contents inside are silently invisible to this walk.
        if _has_win32_unsafe_name(path.name):
            key = str(path)
            if key not in warned_unsafe:
                warned_unsafe.add(key)
                logger.warning(
                    f"Path '{path}' has a trailing dot or whitespace in its "
                    f"name. NTFS preserves it but Win32 GUI tools hide it, "
                    f"and pathlib cannot recurse into trailing-dot folders. "
                    f"Any files INSIDE may be silently missed. Rename to fix."
                )
        if not path.is_file():
            continue
        if _traverses_symlink(path, root):
            continue
        if path.name.lower() in SKIP_FILENAMES:
            continue
        if path.suffix.lower() not in MEDIA_EXTENSIONS:
            continue
        by_dir.setdefault(str(path.parent), []).append(path)
        total += 1
        if limit and total >= limit:
            break

    records: list[FileRecord] = []
    for _directory, files in by_dir.items():
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
    # str(path) for case-sensitivity on Windows — see photo-manager#170.
    paired: set[str] = set()

    for path in files:
        if str(path) in paired:
            continue

        file_type, misnamed = get_file_type(path)
        if file_type == "skip":
            continue

        partner = _find_live_photo_partner(path, stem_map)
        if partner is not None:
            paired.add(str(partner))

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
