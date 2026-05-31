"""Walk source directories and build FileRecord lists with Live Photo pairing."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Optional

from loguru import logger

from scanner.media import (
    EDITED_SUFFIXES,
    MEDIA_EXTENSIONS,
    SKIP_DIRECTORIES,
    SKIP_FILENAMES,
    get_file_type,
    parse_media_filename,
)


def _is_in_skip_directory(path: Path, root: Path) -> bool:
    """Return True if any ancestor of ``path`` between ``root`` and
    ``path`` is in :data:`SKIP_DIRECTORIES` (case-insensitive).

    Catches files inside Windows ``$RECYCLE.BIN`` / ``System Volume
    Information`` / ``.Trashes`` regardless of where the user pointed
    the scan root. The walker's normal filename / extension filters
    would happily pull a recycle-bin ``$Rxxxxxx.jpg`` into the
    manifest — leading to the user-reported "send2trash WinError
    -2147024809" cluster when the user tries to delete via Execute
    Action (already-in-recycle-bin files can't be sent to the
    recycle bin again).

    Args:
        path: The candidate file path (any descendant of ``root``).
        root: The scan root for the current source.
    """
    current = path.parent if path.is_file() else path
    while current != root and current != current.parent:
        if current.name.lower() in SKIP_DIRECTORIES:
            return True
        current = current.parent
    return False


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
    pair_cluster: tuple[Path, ...] = ()  # Peers with same exact stem in same dir
    misnamed: bool = False   # True if magic bytes differ from file extension


def scan_sources(
    sources: dict[str, Path],
    limit: int | None = None,
    recursive_map: dict[str, bool] | None = None,
    progress_callback: Callable[[], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> list[FileRecord]:
    """Walk each source directory and return all discovered FileRecords.

    Args:
        sources: Mapping of label → root path.
        limit: If set, stop after this many files per source (for debug/dry-run).
        recursive_map: Optional per-label recursive flag.  ``True`` (or absent)
            means walk all subdirectories; ``False`` means top-level files only.
            When ``None`` all sources are scanned recursively (original behaviour).
        progress_callback: Optional zero-arg hook fired once each time a
            media file is accepted into the result set (after the
            media-extension + skip-name + symlink filters). Lets the
            caller render a live "Walking sources — N files…" indicator
            on long NAS scans where the synchronous ``rglob`` would
            otherwise sit silent for minutes. See #448.
        cancel_check: Optional zero-arg predicate polled at the top of
            the per-file ``rglob`` loop and between source iterations.
            When it returns ``True`` the walker breaks out and returns
            whatever has been collected so far — partial results, not
            an exception. Lets a UI thread (typically the ScanWorker
            QThread polling its own ``isInterruptionRequested``) stop
            a long NAS walk within one file-tick instead of waiting
            for ``rglob`` to exhaust. See #491.
    """
    records: list[FileRecord] = []
    for label, root in sources.items():
        if cancel_check is not None and cancel_check():
            break
        if not root.exists():
            raise FileNotFoundError(f"Source directory not found: {root}")
        recursive = True if recursive_map is None else recursive_map.get(label, True)
        records.extend(_scan_dir(
            root, label, limit=limit, recursive=recursive,
            progress_callback=progress_callback, cancel_check=cancel_check,
        ))
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


def _iter_tree(root: Path, recursive: bool) -> Iterator[Path]:
    """Yield every entry under ``root`` (files and dirs), skip-on-error.

    #509 — replaces the bare ``root.rglob("*")`` / ``root.glob("*")``
    traversal. ``rglob`` is a *generator*: when recursive descent
    reaches an inaccessible reparse point (a broken symlink/junction,
    e.g. ``node_modules\\.bin\\acorn``), the underlying ``os.scandir``
    raises ``OSError`` [WinError 1920] *from inside* the generator, the
    exception propagates out of the ``for`` loop, ``ScanWorker.run()``
    emits ``failed``, and the ENTIRE scan aborts on the first bad
    entry — even though thousands of good files remain.

    A generator can't resume after raising mid-iteration, so the fix is
    a different traversal primitive. This is a manual ``os.scandir``
    stack: every ``scandir`` call and every ``entry.is_dir()`` probe is
    wrapped in ``try/except OSError`` so one inaccessible entry is
    logged once and skipped while the walk keeps going. The yield order
    matches ``rglob`` closely enough for the existing tests (entries of
    a directory before descending into its subdirectories), and — like
    ``rglob`` — yields BOTH files and directories so the caller's
    per-path guards (Win32-unsafe warning, symlink/skip-dir/extension
    filters) run unchanged.

    Args:
        root: Directory to walk.
        recursive: When ``True`` descend into subdirectories; when
            ``False`` yield only the immediate entries of ``root``.
    """
    try:
        with os.scandir(root) as it:
            entries = list(it)
    except OSError as exc:
        # The root itself (or a subdirectory mid-descent) is
        # inaccessible — log once and skip rather than abort the scan.
        logger.warning(
            f"Skipping unreadable directory '{root}' during scan walk: "
            f"{exc!r}"
        )
        return
    subdirs: list[Path] = []
    for entry in entries:
        path = Path(entry.path)
        yield path
        if not recursive:
            continue
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
        except OSError as exc:
            # Inaccessible reparse point (broken junction/symlink) —
            # the exact WinError 1920 case from #509. Log once, skip.
            logger.warning(
                f"Skipping unreadable entry '{path}' during scan walk: "
                f"{exc!r}"
            )
            continue
        if is_dir:
            subdirs.append(path)
    for subdir in subdirs:
        yield from _iter_tree(subdir, recursive=True)


def _scan_dir(
    root: Path,
    label: str,
    limit: int | None = None,
    recursive: bool = True,
    progress_callback: Callable[[], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> list[FileRecord]:
    """Walk root and return FileRecords with Live Photo pairs resolved.

    Args:
        root: Root directory to scan.
        label: Source label assigned to every returned record.
        limit: Stop after this many files (for debug/dry-run).
        recursive: When ``True`` walk all subdirectories (default); when
            ``False`` scan only the immediate files in ``root``.
        cancel_check: See :func:`scan_sources`. Polled at the top of the
            per-file loop so a cancel lands within one ``rglob`` tick.
    """
    # Collect all media files grouped by directory for efficient pairing.
    # Keys are str(parent), not Path. On Windows pathlib equality is
    # case-INSENSITIVE — two genuinely-distinct sibling directories that
    # differ only by case (rare but possible on case-sensitive NTFS dirs)
    # would collapse and lose one. See photo-manager#170.
    by_dir: dict[str, list[Path]] = {}
    total = 0
    warned_unsafe: set[str] = set()
    for path in _iter_tree(root, recursive=recursive):
        # #491 — cooperative cancel. Polled here (before any per-path
        # work) so the next ``rglob`` tick after a UI-side
        # ``requestInterruption`` breaks out with the partial result
        # already collected. The check is a single Python-level call
        # (typically an attribute read on the QThread), cheap enough
        # to run on every rglob hit including non-media ones.
        if cancel_check is not None and cancel_check():
            break
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
        if _is_in_skip_directory(path, root):
            continue
        if path.name.lower() in SKIP_FILENAMES:
            continue
        if path.suffix.lower() not in MEDIA_EXTENSIONS:
            continue
        by_dir.setdefault(str(path.parent), []).append(path)
        total += 1
        # #448 — fire the progress hook for each accepted media file so
        # the worker can emit a live walking-stage counter on long NAS
        # scans where ``rglob`` would otherwise sit silent.
        if progress_callback is not None:
            progress_callback()
        if limit and total >= limit:
            break

    records: list[FileRecord] = []
    for _directory, files in by_dir.items():
        records.extend(_process_directory(files, label))
    return records


def _process_directory(files: list[Path], label: str) -> list[FileRecord]:
    """Build FileRecords for one directory, computing Live Photo clusters.

    Every media file gets its own FileRecord. Live Photo grouping is
    expressed as a ``pair_cluster``: the tuple of all OTHER non-edited,
    non-skip files in this directory that share the same exact stem
    (clean_stem AND ``(N)`` dupe-marker number). Downstream
    (``scanner/dedup._collect_pair_edges``) emits a union-find edge per
    peer, so the whole cluster collapses into one group_id.

    Why exact-stem matching (not just clean_stem): production data
    contains pairs that share a clean_stem because Google Takeout adds
    ``(N)`` to disambiguate copies from different albums. A naive
    clean_stem match conflates ``IMG_1856.HEIC`` with both
    ``IMG_1856.MP4`` AND ``IMG_1856(1).MP4`` — non-deterministically
    pairing the wrong files. Requiring ``number`` equality keeps the
    two underlying pairs separate.

    Why a cluster (not a single ``pair_partner``): production data also
    contains multi-companion clusters like
    ``IMG_4278.HEIC + IMG_4278.MOV + IMG_4278.MP4`` (Google transcoded
    the same Live Photo to both video formats) and
    ``IMG_5332.HEIC + IMG_5332.MP4 + IMG_5332.jpg`` (a Live Photo with
    an extra JPG variant). All three should land in one group; a
    single-partner field would orphan one of them.

    Pre-#88 this loop additionally maintained a ``paired`` set and
    silently dropped MOV/MP4 partners before hashing. Both bugs are
    fixed here — see the discussion on PR #178.
    """
    # Build (clean_stem, number) → list[Path] using full stem identity.
    # Edited files are excluded from clusters: they neither pair with
    # their non-edited sibling nor with each other (downstream dedup
    # handles edited-vs-original via SHA / pHash).
    cluster_map: dict[tuple[str, Optional[int]], list[Path]] = {}
    for path in files:
        mf = parse_media_filename(path)
        if mf.is_edited:
            continue
        cluster_map.setdefault((mf.clean_stem, mf.number), []).append(path)

    records: list[FileRecord] = []
    for path in files:
        file_type, misnamed = get_file_type(path)
        if file_type == "skip":
            continue
        mf = parse_media_filename(path)
        if mf.is_edited:
            cluster: tuple[Path, ...] = ()
        else:
            peers = cluster_map.get((mf.clean_stem, mf.number), [])
            cluster = tuple(p for p in peers if p != path)
        records.append(FileRecord(
            path=path,
            source_label=label,
            file_type=file_type,
            pair_cluster=cluster,
            misnamed=misnamed,
        ))
    return records


