"""Classify files as KEEP/MOVE/SKIP/REVIEW_DUPLICATE/UNDATED.

Classification rules:
  SHA-256 match                          → SKIP (EXACT_DUPLICATE)
  pHash hamming == 0, both lossy         → SKIP lower priority (FORMAT_DUPLICATE)
  pHash hamming == 0, one RAW + lossy    → MOVE both (complementary)
  pHash hamming 1–threshold              → REVIEW_DUPLICATE
  no EXIF date                           → UNDATED
  otherwise                              → MOVE (or KEEP for iphone source)

Source priority: iphone > takeout > jdrive
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

SOURCE_PRIORITY = {"iphone": 0, "takeout": 1, "jdrive": 2}

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


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify(records: list[HashResult], threshold: int = 10) -> list[ManifestRow]:
    """Assign an action to every record and return ManifestRows.

    iPhone files always receive KEEP (already in place; used as dedup reference).
    """
    rows: dict[Path, ManifestRow] = {}

    # Pass 1: exact SHA-256 duplicates
    _classify_exact(records, rows)

    # Pass 2: pHash-based (cross-format + near-duplicate)
    _classify_phash(records, rows, threshold)

    # Pass 3: remaining unclassified files
    for hr in records:
        if hr.record.path in rows:
            continue
        if hr.record.source_label == "iphone":
            rows[hr.record.path] = _make_row(hr, "KEEP", reason="iphone source — stays in place")
        elif hr.exif_date is None:
            rows[hr.record.path] = _make_row(hr, "UNDATED", reason="no EXIF DateTimeOriginal")
        else:
            rows[hr.record.path] = _make_row(
                hr, "MOVE", reason="unique", dest=_dest_path(hr)
            )

    # Pass 4: propagate SKIP/KEEP to Live Photo MOV partners
    _propagate_pairs(records, rows)

    return list(rows.values())


def _classify_exact(records: list[HashResult], rows: dict[Path, ManifestRow]) -> None:
    """Group by SHA-256; mark lower-priority copies as SKIP."""
    by_hash: dict[str, list[HashResult]] = {}
    for hr in records:
        by_hash.setdefault(hr.sha256, []).append(hr)

    for group in by_hash.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda h: (SOURCE_PRIORITY.get(h.record.source_label, 99),))
        keeper = group[0]
        for duplicate in group[1:]:
            rows[duplicate.record.path] = _make_row(
                duplicate,
                "SKIP",
                duplicate_of=str(keeper.record.path),
                reason=f"EXACT_DUPLICATE of {keeper.record.path.name}",
            )


def _classify_phash(
    records: list[HashResult], rows: dict[Path, ManifestRow], threshold: int
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
        _classify_format_group(group, rows)

    # Near-duplicate scan: compare all pairs with hamming distance ≤ threshold
    # Use bucket approach: compare within pHash prefix buckets to limit O(n²)
    _classify_near_duplicates(candidates, rows, threshold)


def _classify_format_group(group: list[HashResult], rows: dict[Path, ManifestRow]) -> None:
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
        SOURCE_PRIORITY.get(h.record.source_label, 99),
    ))
    keeper = lossy[0]
    for duplicate in lossy[1:]:
        if duplicate.record.path in rows:
            continue
        rows[duplicate.record.path] = _make_row(
            duplicate,
            "SKIP",
            duplicate_of=str(keeper.record.path),
            hamming=0,
            reason=f"FORMAT_DUPLICATE of {keeper.record.path.name} "
                   f"({duplicate.record.file_type} vs {keeper.record.file_type})",
        )


def _classify_near_duplicates(
    candidates: list[HashResult], rows: dict[Path, ManifestRow], threshold: int
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
                    key=lambda h: SOURCE_PRIORITY.get(h.record.source_label, 99),
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
        if own_row.action in ("SKIP", "KEEP"):
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
    return ManifestRow(
        source_path=str(hr.record.path),
        source_label=hr.record.source_label,
        dest_path=dest,
        action=action,
        source_hash=hr.sha256,
        phash=hr.phash,
        hamming_distance=int(hamming) if hamming is not None else None,
        duplicate_of=duplicate_of,
        reason=reason,
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
