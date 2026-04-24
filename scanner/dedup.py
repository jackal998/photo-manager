"""Classify files as MOVE/EXACT/REVIEW_DUPLICATE/UNDATED.

Classification rules:
  SHA-256 match                          → EXACT (exact duplicate)
  pHash hamming == 0, both lossy         → EXACT lower priority (format duplicate)
  pHash hamming == 0, one RAW + lossy    → MOVE both (complementary)
  pHash hamming 1–threshold              → REVIEW_DUPLICATE
  no EXIF date                           → UNDATED
  otherwise                              → MOVE

Source priority: positional (index 0 = highest priority).
  Pass ``source_priority`` dict to ``classify()``; omit it for auto-inference
  from the order labels first appear in the input records.
Format priority (lossy only): heic > jpeg > png > others
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import imagehash
    _IMAGEHASH_AVAILABLE = True
except ImportError:
    _IMAGEHASH_AVAILABLE = False

from scanner.media import RAW_EXTENSIONS
from scanner.walker import FileRecord

# ---------------------------------------------------------------------------
# Priority tables
# ---------------------------------------------------------------------------

FORMAT_PRIORITY = {"heic": 0, "jpeg": 1, "png": 2, "gif": 3, "webp": 4, "raw": -1}
# raw is intentionally -1 (not comparable with lossy — RAW+lossy always co-exist)

LOSSY_TYPES = {"jpeg", "heic", "png", "gif", "webp"}

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class HashResult:
    """A FileRecord augmented with computed hashes and EXIF date."""

    record: FileRecord
    sha256: str
    phash: Optional[str]       # None for video or hash failure
    exif_date: Optional[datetime]


@dataclass
class ManifestRow:
    """One row destined for migration_manifest.sqlite."""

    source_path: str
    source_label: str
    dest_path: Optional[str]   # relative path under dest root; None if SKIP/UNDATED
    action: str                # KEEP | MOVE | SKIP | REVIEW_DUPLICATE | UNDATED
    source_hash: str
    phash: Optional[str]
    hamming_distance: Optional[int]
    duplicate_of: Optional[str]
    reason: str
    # Cached at scan time — eliminates all filesystem I/O at load time
    file_size_bytes: Optional[int] = None
    shot_date: Optional[str] = None      # ISO 8601 from EXIF DateTimeOriginal
    creation_date: Optional[str] = None  # ISO 8601 filesystem ctime
    mtime: Optional[str] = None          # ISO 8601 filesystem mtime


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify(
    records: list[HashResult],
    threshold: int = 10,
    source_priority: dict[str, int] | None = None,
) -> list[ManifestRow]:
    """Assign an action to every record and return ManifestRows.

    Args:
        records: All hashed file records to classify.
        threshold: Maximum Hamming distance to flag as REVIEW_DUPLICATE.
        source_priority: Mapping of source label → priority integer (lower wins).
            When ``None``, priority is inferred from the order labels first appear
            in ``records`` (first seen = priority 0).
    """
    if source_priority is None:
        seen: dict[str, int] = {}
        for hr in records:
            label = hr.record.source_label
            if label not in seen:
                seen[label] = len(seen)
        source_priority = seen

    rows: dict[Path, ManifestRow] = {}

    # Pass 1: exact SHA-256 duplicates
    _classify_exact(records, rows, source_priority)

    # Pass 2: pHash-based (cross-format + near-duplicate)
    _classify_phash(records, rows, threshold, source_priority)

    # Pass 3: remaining unclassified files — all sources treated equally
    for hr in records:
        if hr.record.path in rows:
            continue
        if hr.exif_date is None:
            rows[hr.record.path] = _make_row(hr, "UNDATED", reason="no EXIF DateTimeOriginal")
        else:
            rows[hr.record.path] = _make_row(
                hr, "MOVE", reason="unique", dest=_dest_path(hr)
            )

    # Pass 4: propagate EXACT/KEEP actions to Live Photo MOV partners
    _propagate_pairs(records, rows)

    return list(rows.values())


def _priority(label: str, source_priority: dict[str, int]) -> int:
    """Return sort priority for a source label (lower integer = higher priority)."""
    return source_priority.get(label, len(source_priority))


def _classify_exact(
    records: list[HashResult],
    rows: dict[Path, ManifestRow],
    source_priority: dict[str, int],
) -> None:
    """Group by SHA-256; mark lower-priority copies as EXACT."""
    by_hash: dict[str, list[HashResult]] = {}
    for hr in records:
        by_hash.setdefault(hr.sha256, []).append(hr)

    for group in by_hash.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda h: _priority(h.record.source_label, source_priority))
        keeper = group[0]
        for duplicate in group[1:]:
            rows[duplicate.record.path] = _make_row(
                duplicate,
                "EXACT",
                duplicate_of=str(keeper.record.path),
                reason=f"exact duplicate of {keeper.record.path.name}",
            )


def _classify_phash(
    records: list[HashResult],
    rows: dict[Path, ManifestRow],
    threshold: int,
    source_priority: dict[str, int],
) -> None:
    """Group by pHash; classify FORMAT_DUPLICATE and REVIEW_DUPLICATE."""
    # Only consider records not already classified and with a valid pHash
    candidates = [hr for hr in records if hr.phash and hr.record.path not in rows]

    # Build pHash → records map (exact matches first)
    by_phash: dict[str, list[HashResult]] = {}
    for hr in candidates:
        by_phash.setdefault(hr.phash, []).append(hr)

    # Exact pHash match (hamming == 0) → FORMAT_DUPLICATE or complementary RAW+lossy
    for group in by_phash.values():
        if len(group) < 2:
            continue
        _classify_format_group(group, rows, source_priority)

    # Near-duplicate scan: compare all pairs with hamming distance ≤ threshold
    _classify_near_duplicates(candidates, rows, threshold, source_priority)


def _classify_format_group(
    group: list[HashResult],
    rows: dict[Path, ManifestRow],
    source_priority: dict[str, int],
) -> None:
    """Within a pHash==0 group, apply RAW+lossy exception and format priority."""
    has_raw = any(hr.record.file_type == "raw" for hr in group)
    lossy = [hr for hr in group if hr.record.file_type in LOSSY_TYPES]

    if has_raw and lossy:
        # RAW + lossy: complementary — all MOVE, don't skip anything
        return

    if len(lossy) < 2:
        return

    # All lossy FORMAT_DUPLICATE: keep highest-format × highest-source-priority
    lossy.sort(key=lambda h: (
        FORMAT_PRIORITY.get(h.record.file_type, 99),
        _priority(h.record.source_label, source_priority),
    ))
    keeper = lossy[0]
    for duplicate in lossy[1:]:
        if duplicate.record.path in rows:
            continue
        rows[duplicate.record.path] = _make_row(
            duplicate,
            "EXACT",
            duplicate_of=str(keeper.record.path),
            hamming=0,
            reason=f"format duplicate of {keeper.record.path.name} "
                   f"({duplicate.record.file_type} vs {keeper.record.file_type})",
        )


def _classify_near_duplicates(
    candidates: list[HashResult],
    rows: dict[Path, ManifestRow],
    threshold: int,
    source_priority: dict[str, int],
) -> None:
    """Flag pHash pairs with hamming distance 1–threshold as REVIEW_DUPLICATE."""
    if not _IMAGEHASH_AVAILABLE:
        return

    unclassified = [hr for hr in candidates if hr.record.path not in rows]
    hashes = [(hr, imagehash.hex_to_hash(hr.phash)) for hr in unclassified if hr.phash]

    for i, (hr_a, hash_a) in enumerate(hashes):
        if hr_a.record.path in rows:
            continue
        for hr_b, hash_b in hashes[i + 1:]:
            if hr_b.record.path in rows:
                continue
            distance = hash_a - hash_b
            if 0 < distance <= threshold:
                # Flag the lower-priority file as REVIEW_DUPLICATE
                ordered = sorted(
                    [hr_a, hr_b],
                    key=lambda h: _priority(h.record.source_label, source_priority),
                )
                flagged = ordered[1]
                if flagged.record.path not in rows:
                    rows[flagged.record.path] = _make_row(
                        flagged,
                        "REVIEW_DUPLICATE",
                        duplicate_of=str(ordered[0].record.path),
                        hamming=distance,
                        reason=f"near-duplicate (hamming={distance}) of "
                               f"{ordered[0].record.path.name}",
                    )


def _propagate_pairs(records: list[HashResult], rows: dict[Path, ManifestRow]) -> None:
    """Propagate SKIP/KEEP actions to Live Photo MOV partners.

    Always overrides the partner's existing action — the image file is authoritative
    for the pair. If the image is SKIP, the MOV must also be SKIP even if it was
    independently classified as MOVE.
    """
    path_to_hr = {hr.record.path: hr for hr in records}

    for hr in records:
        partner_path = hr.record.pair_partner
        if partner_path is None:
            continue
        own_row = rows.get(hr.record.path)
        if own_row is None:
            continue
        if own_row.action in ("EXACT", "KEEP"):
            partner_hr = path_to_hr.get(partner_path)
            if partner_hr:
                rows[partner_path] = _make_row(
                    partner_hr,
                    own_row.action,
                    duplicate_of=own_row.duplicate_of or str(hr.record.path),
                    reason=f"Live Photo pair partner of {hr.record.path.name}",
                )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(
    hr: HashResult,
    action: str,
    reason: str = "",
    duplicate_of: Optional[str] = None,
    hamming: Optional[int] = None,
    dest: Optional[str] = None,
) -> ManifestRow:
    import os
    from datetime import datetime as _dt
    from infrastructure.utils import get_filesystem_creation_datetime

    path_str = str(hr.record.path)
    try:
        _size: Optional[int] = os.path.getsize(path_str)
    except OSError:
        _size = None
    try:
        _mtime: Optional[str] = _dt.fromtimestamp(os.path.getmtime(path_str)).isoformat()
    except OSError:
        _mtime = None
    _ctime = get_filesystem_creation_datetime(path_str)
    _shot: Optional[str] = hr.exif_date.isoformat() if hr.exif_date else None

    return ManifestRow(
        source_path=path_str,
        source_label=hr.record.source_label,
        dest_path=dest,
        action=action,
        source_hash=hr.sha256,
        phash=hr.phash,
        hamming_distance=int(hamming) if hamming is not None else None,
        duplicate_of=duplicate_of,
        reason=reason,
        file_size_bytes=_size,
        shot_date=_shot,
        creation_date=_ctime.isoformat() if _ctime else None,
        mtime=_mtime,
    )


def _dest_path(hr: HashResult) -> Optional[str]:
    """Compute relative destination path for a MOVE action."""
    if hr.exif_date is None:
        return None
    year = hr.exif_date.strftime("%Y")
    date_prefix = hr.exif_date.strftime("%Y%m%d")
    label = hr.record.source_label
    filename = hr.record.path.name
    return f"{year}/{date_prefix}_{label}/{filename}"
