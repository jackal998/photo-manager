"""Classify files as EXACT/REVIEW_DUPLICATE/UNDATED or undecided ("").

Classification rules:
  SHA-256 match                          → EXACT (exact duplicate)
  pHash hamming == 0, both lossy         → EXACT lower priority (format duplicate)
  pHash hamming == 0, one RAW + lossy    → "" both (complementary, undecided)
  pHash hamming 1–threshold              → REVIEW_DUPLICATE
  no EXIF date                           → UNDATED
  otherwise                              → "" (undecided non-duplicate file)

The legacy ``MOVE`` action and ``dest_path`` column were the handshake to
the now-defunct external photo-transfer tool; they were removed in #433.
Unique, dated, non-duplicate files now carry the empty action ("") — the
canonical "undecided" state the review UI already renders as a Ref-tier row.

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
    dhash: Optional[str] = None        # #517 — second perceptual hash (gradient); None when phash is None
    mean_color: Optional[str] = None   # "R,G,B" average pixel; None for video/RAW/failure
    pixel_width: Optional[int] = None  # image width in pixels; None for video/failure
    pixel_height: Optional[int] = None # image height in pixels; None for video/failure

    def to_media_extract(self) -> "MediaExtract":  # noqa: F821 — runtime import
        """Convert this HashResult into a partial MediaExtract (#187 — PR 2).

        Used by the scan pipeline to feed the merge step alongside the
        exiftool partial. The ``extracted_by`` set is populated per the
        tool(s) that actually contributed data:

        * ``"hasher"`` — always (sha256 always present).
        * ``"pil"`` — when phash was computed (i.e. PIL opened the bytes).
          For RAW files this means PIL opened the rawpy-extracted
          thumbnail.
        * ``"rawpy"`` — added for RAW files when sensor dimensions were
          read; ``merge_extracts`` uses this to prefer rawpy's sensor
          dims over PIL's thumbnail dims.
        """
        from scanner.media_extract import MediaExtract

        extracted: set[str] = {"hasher"}
        if self.phash is not None:
            extracted.add("pil")
        if (
            self.record.file_type == "raw"
            and self.pixel_width is not None
        ):
            extracted.add("rawpy")

        return MediaExtract(
            path=self.record.path,
            file_type=self.record.file_type,
            sha256=self.sha256,
            phash=self.phash,
            mean_color=self.mean_color,
            pixel_width=self.pixel_width,
            pixel_height=self.pixel_height,
            exif_date=self.exif_date,
            # exif_date_tag intentionally None — PIL doesn't surface which
            # tag produced the date. The exiftool partial will fill it.
            extracted_by=extracted,
        )


@dataclass
class ManifestRow:
    """One row destined for migration_manifest.sqlite."""

    source_path: str
    source_label: str
    action: str                # "" (undecided) | EXACT | REVIEW_DUPLICATE | UNDATED
    source_hash: str
    phash: Optional[str]
    hamming_distance: Optional[int]
    duplicate_of: Optional[str]   # transient — used for union-find edges; NOT written to DB
    reason: str
    # Cached at scan time — eliminates all filesystem I/O at load time
    file_size_bytes: Optional[int] = None
    shot_date: Optional[str] = None      # ISO 8601 from EXIF DateTimeOriginal
    creation_date: Optional[str] = None  # ISO 8601 filesystem ctime
    mtime: Optional[str] = None          # ISO 8601 filesystem mtime
    group_id: Optional[str] = None       # canonical root path of connected component; written to DB
    pixel_width: Optional[int] = None    # image width in pixels; written to DB
    pixel_height: Optional[int] = None   # image height in pixels; written to DB
    # Scoring system (#187) — raw signals + composite score. Populated by
    # the extended exiftool pass (PR 2) and scorer (PR 3/4). All default to
    # None/False so existing constructors continue to work unchanged.
    exif_tag_count: Optional[int] = None # count of census EXIF/XMP/QuickTime tags present
    gps_present: bool = False            # True if GPSLatitude tag present
    xmp_derived: bool = False            # True if xmpMM:DerivedFrom tag present (file is a derivative)
    score: Optional[float] = None        # composite quality score in [0.0, 1.0]; NULL for isolated or unscored rows
    # #517 — multi-hash confidence: "high" when an independent second hash
    # (dHash) agrees with the pHash match (or the match is SHA-exact), "low"
    # when only pHash agrees (dHash missing or disagrees), None for non-dup
    # rows. Transient like ``duplicate_of`` — NOT written to DB in this phase;
    # it drives the auto-select aggressive-delete gate (low-confidence rows
    # are never auto-marked for deletion). Persistence + a UI badge are a
    # tracked follow-up.
    match_confidence: Optional[str] = None


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _mean_color_distance(a: str, b: str) -> float:
    """L2 distance between two mean-color strings ("R,G,B")."""
    ra, ga, ba = (int(x) for x in a.split(","))
    rb, gb, bb = (int(x) for x in b.split(","))
    return ((ra - rb) ** 2 + (ga - gb) ** 2 + (ba - bb) ** 2) ** 0.5


def _dhash_confidence(a: HashResult, b: HashResult, dhash_threshold: int) -> str:
    """#517 — confidence in a pHash-based duplicate match, voted by dHash.

    pHash (DCT/frequency) and dHash (gradient/brightness) capture different
    image structure, so requiring a *second, independent* hash to agree is a
    precision signal. Returns ``"high"`` when dHash also agrees within
    ``dhash_threshold``; ``"low"`` when only pHash agreed (dHash missing on
    either side, imagehash unavailable, or dHash beyond threshold).

    The match is NOT dropped on a "low" vote — grouping stays pHash-driven —
    but the flag lets downstream consumers (auto-select) treat a pHash-only
    match cautiously instead of auto-deleting it.
    """
    if not a.dhash or not b.dhash or not _IMAGEHASH_AVAILABLE:
        return "low"
    try:
        distance = imagehash.hex_to_hash(a.dhash) - imagehash.hex_to_hash(b.dhash)
    except (ValueError, TypeError):
        return "low"
    return "high" if distance <= dhash_threshold else "low"


def classify(
    records: list[HashResult],
    threshold: int = 10,
    mean_color_threshold: int = 30,
    source_priority: dict[str, int] | None = None,
    min_phash_entropy_bits: int = 4,
    dhash_threshold: int = 10,
    min_phash_dimension: int = 128,
) -> list[ManifestRow]:
    """Assign an action to every record and return ManifestRows.

    Args:
        records: All hashed file records to classify.
        threshold: Maximum Hamming distance to flag as REVIEW_DUPLICATE.
        mean_color_threshold: L2 distance gate for mean-color false-positive rejection.
            0 disables the gate; higher values are more permissive.
        source_priority: Mapping of source label → priority integer (lower wins).
            When ``None``, priority is inferred from the order labels first appear
            in ``records`` (first seen = priority 0).
        min_phash_entropy_bits: pHash-entropy guard (#516). A pHash whose set-bit
            count is within this many bits of either extreme (0 or nbits) is
            degenerate — a flat/near-empty image whose hash collides with every
            other flat image — and is excluded from pHash near-dup grouping (it
            still participates in exact-SHA dedup). ``0`` disables the guard.
        dhash_threshold: Maximum dHash Hamming distance for the #517 confidence
            vote. A pHash match whose dHash also agrees within this distance is
            flagged ``match_confidence="high"``, otherwise ``"low"``. Does NOT
            change which rows are grouped — only the confidence flag.
        min_phash_dimension: dimension gate. An image whose smaller side is
            below this many pixels is too low-detail for a 64-bit pHash to
            discriminate — tiny UI icons / thumbnails flatten to a handful of
            DCT coefficients, so unrelated small images collide and form false
            near-dup groups. Such files are excluded from pHash near-dup
            grouping (exact-SHA dedup still applies); images with unknown
            dimensions (video / decode failure) pass through. ``0`` disables.
    """
    if source_priority is None:
        seen: dict[str, int] = {}
        for hr in records:
            label = hr.record.source_label
            if label not in seen:
                seen[label] = len(seen)
        source_priority = seen

    # Keys are str(path), not Path. On Windows pathlib equality is case-INSENSITIVE
    # (e.g. Path("a.MOV") == Path("a.mov") is True), which silently collapses
    # genuinely-distinct files that happen to share a name in different case on
    # case-sensitive NTFS dirs. See photo-manager#170.
    rows: dict[str, ManifestRow] = {}

    # Pass 1: exact SHA-256 duplicates
    _classify_exact(records, rows, source_priority)

    # Pass 2: pHash-based (cross-format + near-duplicate)
    _classify_phash(
        records, rows, threshold, source_priority, mean_color_threshold,
        min_phash_entropy_bits, dhash_threshold, min_phash_dimension,
    )

    # Pass 3: remaining unclassified files — all sources treated equally
    for hr in records:
        key = str(hr.record.path)
        if key in rows:
            continue
        if hr.exif_date is None:
            rows[key] = _make_row(hr, "UNDATED", reason="no EXIF DateTimeOriginal")
        else:
            rows[key] = _make_row(hr, "", reason="unique")

    # Pass 4 (was: action propagation, removed in #88): collect Live Photo
    # pair edges. Pairs always share a group_id, but each row keeps its
    # independent action / user_decision — the image's classification no
    # longer dictates the video's.
    pair_edges = _collect_pair_edges(records, rows)

    # Pass 5: assign group_id via union-find over duplicate_of + pair edges.
    _assign_group_ids(rows, pair_edges)

    return list(rows.values())


def _priority(label: str, source_priority: dict[str, int]) -> int:
    """Return sort priority for a source label (lower integer = higher priority)."""
    return source_priority.get(label, len(source_priority))


def _classify_exact(
    records: list[HashResult],
    rows: dict[str, ManifestRow],
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
            rows[str(duplicate.record.path)] = _make_row(
                duplicate,
                "EXACT",
                duplicate_of=str(keeper.record.path),
                reason=f"exact duplicate of {keeper.record.path.name}",
                match_confidence="high",  # #517 — byte-identical SHA match is certain
            )


def _phash_entropy_ok(phash: str, min_bits: int) -> bool:
    """True if a pHash carries enough structure to be a trustworthy
    near-duplicate signal (#516).

    A flat / near-empty image (solid colour, blank scan, UI icon,
    letterboxed frame) has almost no DCT AC energy, so its perceptual
    hash degenerates to all-zeros (``0000000000000000``) or all-ones —
    a value that collides with *every* other flat image regardless of
    content. Used as a near-dup signal, such a hash is a super-connector
    that merges unrelated files into one false group. We therefore
    distrust pHashes whose set-bit count is within ``min_bits`` of either
    extreme (0 or nbits) and exclude them from pHash grouping; they still
    participate in exact-SHA dedup (Pass 1) and fall through to the
    undecided/UNDATED passes.

    ``min_bits <= 0`` disables the guard (pre-#516 behaviour). An
    unparseable hash is treated as OK — the downstream passes already
    tolerate odd values, and suppressing them would lose real matches.
    """
    if min_bits <= 0:
        return True
    try:
        bits = bin(int(phash, 16)).count("1")
    except (ValueError, TypeError):
        return True
    nbits = len(phash) * 4
    return min_bits <= bits <= nbits - min_bits


def _phash_dimension_ok(hr: HashResult, min_dimension: int) -> bool:
    """True if the image is large enough for a 64-bit pHash to discriminate.

    A tiny image (UI icon, thumbnail, sprite) downsamples to a handful of DCT
    coefficients, so a 64-bit pHash can't tell unrelated small images apart —
    e.g. a 24×24 button icon and a 24×24 checkbox icon land within the
    near-dup threshold and form a false group, *even though* their pHash isn't
    degenerate and their dHash agrees (so #516 / #517 don't catch them). A
    photo manager only ever wants to perceptually-group photo-sized images, so
    an image whose smaller side is below ``min_dimension`` is kept out of
    pHash grouping (exact-SHA dedup still applies).

    ``min_dimension <= 0`` disables the gate. Unknown dimensions (video /
    decode failure → ``pixel_width``/``pixel_height`` is ``None``) pass
    through — the same None-tolerant handling as the other guards.
    """
    if min_dimension <= 0:
        return True
    w, h = hr.pixel_width, hr.pixel_height
    if w is None or h is None:
        return True
    return min(w, h) >= min_dimension


def _classify_phash(
    records: list[HashResult],
    rows: dict[str, ManifestRow],
    threshold: int,
    source_priority: dict[str, int],
    mean_color_threshold: int = 30,
    min_phash_entropy_bits: int = 4,
    dhash_threshold: int = 10,
    min_phash_dimension: int = 128,
) -> None:
    """Group by pHash; classify FORMAT_DUPLICATE and REVIEW_DUPLICATE.

    Records are excluded from BOTH the exact-pHash format-group path and the
    near-duplicate scan when their pHash is degenerate (flat image — see
    :func:`_phash_entropy_ok`, #516) OR the image is too small for a 64-bit
    pHash to discriminate (:func:`_phash_dimension_ok` — tiny UI icons /
    thumbnails). Both classes still get exact-SHA dedup.
    """
    # Only consider records not already classified, with a valid pHash,
    # whose pHash is trustworthy (#516 entropy) and whose image is large
    # enough to discriminate (dimension gate — kills tiny-icon false groups).
    candidates = [
        hr for hr in records
        if hr.phash
        and _phash_entropy_ok(hr.phash, min_phash_entropy_bits)
        and _phash_dimension_ok(hr, min_phash_dimension)
        and str(hr.record.path) not in rows
    ]

    # Build pHash → records map (exact matches first)
    by_phash: dict[str, list[HashResult]] = {}
    for hr in candidates:
        by_phash.setdefault(hr.phash, []).append(hr)

    # Exact pHash match (hamming == 0) → FORMAT_DUPLICATE or complementary RAW+lossy
    for group in by_phash.values():
        if len(group) < 2:
            continue
        _classify_format_group(
            group, rows, source_priority, mean_color_threshold, dhash_threshold
        )

    # Near-duplicate scan: compare all pairs with hamming distance ≤ threshold
    _classify_near_duplicates(
        candidates, rows, threshold, source_priority, mean_color_threshold,
        dhash_threshold,
    )


def _classify_format_group(
    group: list[HashResult],
    rows: dict[str, ManifestRow],
    source_priority: dict[str, int],
    mean_color_threshold: int = 30,
    dhash_threshold: int = 10,
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
        key = str(duplicate.record.path)
        if key in rows:
            continue
        # #462 — mean-color gate on the exact-pHash path. Catches the
        # catalogued domain pattern where flat images (solid color or
        # near-empty composition) all collide on pHash 8000000000000000
        # — without this guard, an arbitrary set of black / white / blank
        # PNGs would be silently marked as EXACT duplicates of each other.
        # Mirrors the gate already in _classify_near_duplicates (line 319);
        # skipped when either side lacks mean_color (RAW thumbnail, hash
        # failure) — matches the near-duplicate path's None-handling.
        if mean_color_threshold > 0 and keeper.mean_color and duplicate.mean_color:
            if _mean_color_distance(keeper.mean_color, duplicate.mean_color) > mean_color_threshold:
                continue
        rows[key] = _make_row(
            duplicate,
            "EXACT",
            duplicate_of=str(keeper.record.path),
            hamming=0,
            reason=f"format duplicate of {keeper.record.path.name} "
                   f"({duplicate.record.file_type} vs {keeper.record.file_type})",
            match_confidence=_dhash_confidence(keeper, duplicate, dhash_threshold),
        )


def _classify_near_duplicates(
    candidates: list[HashResult],
    rows: dict[str, ManifestRow],
    threshold: int,
    source_priority: dict[str, int],
    mean_color_threshold: int = 30,
    dhash_threshold: int = 10,
) -> None:
    """Flag pHash pairs with hamming distance 1–threshold as REVIEW_DUPLICATE."""
    if not _IMAGEHASH_AVAILABLE:
        return

    unclassified = [hr for hr in candidates if str(hr.record.path) not in rows]
    hashes = [(hr, imagehash.hex_to_hash(hr.phash)) for hr in unclassified if hr.phash]

    for i, (hr_a, hash_a) in enumerate(hashes):
        # Do NOT skip hr_a when it is already classified — it can still serve as
        # a comparator so that transitively-similar files (hr_b similar to hr_a
        # which is similar to an earlier file) are connected into the same group.
        for hr_b, hash_b in hashes[i + 1:]:
            if str(hr_b.record.path) in rows:
                continue
            distance = hash_a - hash_b
            if 0 < distance <= threshold:
                # Mean-color gate: reject if average colors clearly differ.
                # Catches pHash false positives (similar DCT structure, different colors).
                # Gate is skipped when either file lacks mean_color (RAW, hash failure).
                if mean_color_threshold > 0 and hr_a.mean_color and hr_b.mean_color:
                    if _mean_color_distance(hr_a.mean_color, hr_b.mean_color) > mean_color_threshold:
                        continue
                # Flag the lower-priority file as REVIEW_DUPLICATE
                ordered = sorted(
                    [hr_a, hr_b],
                    key=lambda h: _priority(h.record.source_label, source_priority),
                )
                flagged = ordered[1]
                flagged_key = str(flagged.record.path)
                if flagged_key not in rows:
                    rows[flagged_key] = _make_row(
                        flagged,
                        "REVIEW_DUPLICATE",
                        duplicate_of=str(ordered[0].record.path),
                        hamming=distance,
                        reason=f"near-duplicate (hamming={distance}) of "
                               f"{ordered[0].record.path.name}",
                        match_confidence=_dhash_confidence(
                            ordered[0], flagged, dhash_threshold
                        ),
                    )


def _collect_pair_edges(
    records: list[HashResult], rows: dict[str, ManifestRow]
) -> list[tuple[str, str]]:
    """Return ``(own_path, peer_path)`` edges for every Live Photo cluster
    member that survived classification.

    Per photo-manager#88: files with the same exact stem (clean_stem AND
    ``(N)`` dupe-marker number) in the same directory always share a
    ``group_id`` regardless of whether any side is itself a duplicate
    of something else. Action / ``user_decision`` /
    ``reason`` are NOT propagated — each row keeps its independent
    classification. The image's destruction is no longer automatically
    the video's; the user makes those decisions per-row in the UI.

    A "cluster" is computed by the walker's ``pair_cluster`` field and
    typically holds one peer (the simple HEIC+MP4 case observed across
    most production data). Larger clusters surface for:

    * HEIC + MOV + MP4 (Google transcoded one Live Photo into both
      video formats — same exact stem, no ``(N)``).
    * HEIC + JPG + MP4 (a Live Photo with an extra image variant).

    All cluster members share one group_id via the edges emitted here.

    Implementation: emit edges as a list (not in-place mutation of
    ``duplicate_of``) so ``_assign_group_ids`` can transitively close
    every component via union-find — handles the asymmetric case where
    each cluster member sits in its own SHA-group, plus the cluster
    edges union them all.

    The walker computes the cluster symmetrically (every member sees
    every other member as a peer), so duplicate edges flow in both
    directions. Union-find dedupes them implicitly.
    """
    edges: list[tuple[str, str]] = []
    for hr in records:
        own_key = str(hr.record.path)
        if own_key not in rows:
            continue
        for peer_path in hr.record.pair_cluster:
            peer_key = str(peer_path)
            if peer_key not in rows:
                continue
            edges.append((own_key, peer_key))
    return edges


def _assign_group_ids(
    rows: dict[str, ManifestRow],
    pair_edges: list[tuple[str, str]] | None = None,
) -> None:
    """Assign group_id via union-find over duplicate_of edges + pair edges.

    Files transitively connected (A→B, B→C) all receive the same group_id —
    the lexicographically smallest source_path in the component.
    Isolated files (no similarity edge AND no pair edge) receive
    group_id = None.

    ``pair_edges`` are Live Photo HEIC ↔ MOV/MP4 pairs from
    ``_collect_pair_edges`` (photo-manager#88). They participate in the
    same union-find as ``duplicate_of`` edges, so a unique pair (neither
    side a duplicate of anything else) still gets its own 2-row group;
    a pair where each side belongs to a different SHA-group still
    transitively closes into a single component.
    """
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while x in parent:
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            if ra < rb:
                parent[rb] = ra
            else:
                parent[ra] = rb

    for row in rows.values():
        if row.duplicate_of:
            union(row.source_path, row.duplicate_of)

    # Pair edges (#88): pair partners always share a group regardless
    # of whether either side is itself a SHA/pHash duplicate.
    for a, b in (pair_edges or []):
        union(a, b)

    # Collect every path that participates in at least one edge
    has_edge: set[str] = set()
    for row in rows.values():
        if row.duplicate_of:
            has_edge.add(row.source_path)
            has_edge.add(row.duplicate_of)
    for a, b in (pair_edges or []):
        has_edge.add(a)
        has_edge.add(b)

    for row in rows.values():
        if row.source_path in has_edge:
            row.group_id = find(row.source_path)
        # else: stays None (isolated file)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(
    hr: HashResult,
    action: str,
    reason: str = "",
    duplicate_of: Optional[str] = None,
    hamming: Optional[int] = None,
    match_confidence: Optional[str] = None,
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
        pixel_width=hr.pixel_width,
        pixel_height=hr.pixel_height,
        match_confidence=match_confidence,
    )
