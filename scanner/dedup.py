"""Classify files as EXACT/REVIEW_DUPLICATE/UNDATED or undecided ("").

Classification rules:
  SHA-256 match                          → EXACT (exact duplicate)
  pHash hamming == 0, both lossy         → EXACT lower priority (format duplicate)
  pHash hamming == 0, both lossy, shot times disagree or both PNG
                                         → REVIEW_DUPLICATE, confidence low
                                            (dedup-core research 2026-11-05 class B)
  pHash hamming == 0, one RAW + lossy    → "" both (complementary, undecided,
                                            but one group_id — #824)
  pHash hamming == 0, all RAW            → "" all (same, #824)
  pHash hamming 1–threshold AND dHash agrees → REVIEW_DUPLICATE (#524 gate)
  pHash near-dup, shot times disagree or both PNG → REVIEW_DUPLICATE, confidence
                                            low (dedup-core research 2026-11-05 class B)
  no EXIF date                           → UNDATED
  otherwise                              → "" (undecided non-duplicate file)

A pHash near-dup is only a *candidate*: the independent dHash must also agree
(#524) — different scenes that collide on the coarse 64-bit pHash disagree on
dHash by a wide margin, so requiring agreement kills those false positives
without costing real near-duplicates. dHash-unavailable pairs fall back to
pHash-only (low confidence).

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
from typing import Callable, Optional

try:
    import imagehash
    _IMAGEHASH_AVAILABLE = True
except ImportError:
    _IMAGEHASH_AVAILABLE = False

from scanner.media import RAW_EXTENSIONS
from scanner.phash_distance import hamming_distance
from scanner.walker import FileRecord

# ---------------------------------------------------------------------------
# Priority tables
# ---------------------------------------------------------------------------

FORMAT_PRIORITY = {"heic": 0, "jpeg": 1, "png": 2, "gif": 3, "webp": 4, "raw": -1}
# raw is intentionally -1 (not comparable with lossy — RAW+lossy always co-exist)

LOSSY_TYPES = {"jpeg", "heic", "png", "gif", "webp"}

# A union-find edge: (path_a, path_b, confidence), confidence "high" or "low".
# "high" edges merge groups. "low" edges are hints: they may attach a lone file
# to a group, but never merge two groups that already have two or more members
# (see ``_assign_group_ids``).
Edge = tuple[str, str, str]

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
    # #786 — exif_tag_count/gps_present/xmp_derived/exif_date_tag from the
    # in-memory PIL pass (scanner.exif.extract_pil_scoring_signals), for
    # formats in hasher._INMEMORY_EXIF_TYPES only. None for every other
    # format — those still get their scoring signals from exiftool.
    inmemory_signals: Optional[dict] = None
    # Recipe "5" — population std-dev of the 32×32 grey working image (the
    # image pHash hashes). Low = flat/near-black, so a degenerate pHash; the
    # ``_phash_quality_ok`` grouping gate reads it. None for video / legacy
    # manifests / hash failure — those pass the gate (None-tolerant).
    phash_quality: Optional[float] = None

    def to_media_extract(self) -> "MediaExtract":  # noqa: F821 — runtime import
        """Convert this HashResult into a MediaExtract (#187 — PR 2).

        Used directly by ``core/app_service/scan_runner.py``'s
        ``_route_outcome`` for formats whose scoring signals come from the
        in-memory hash pass instead of exiftool (JPEG, #786): when
        ``self.inmemory_signals`` is set, the returned extract is layered
        with ``exif_date`` / ``exif_date_tag`` / ``exif_tag_count`` /
        ``gps_present`` / ``xmp_derived`` from it and written straight into
        the pipeline's ``extracts`` dict — no exiftool call for this file
        at all. The ``extracted_by`` set is populated per the tool(s) that
        actually contributed data:

        * ``"hasher"`` — always (sha256 always present).
        * ``"pil"`` — when phash was computed (i.e. PIL opened the bytes).
          For RAW files this means PIL opened the rawpy-extracted
          thumbnail.
        * ``"rawpy"`` — added for RAW files when sensor dimensions were
          read (the sensor dims, not PIL's embedded-thumbnail dims).
        * ``"pil_exif_inmemory"`` — added when ``inmemory_signals`` was
          available (#786).
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

        exif_date = self.exif_date
        exif_date_tag = None  # PIL doesn't surface which tag produced the date.
        exif_tag_count = None
        gps_present = None
        xmp_derived = None
        subsec_time_original = None
        offset_time_original = None
        if self.inmemory_signals is not None:
            extracted.add("pil_exif_inmemory")
            exif_date = self.inmemory_signals["exif_date"]
            exif_date_tag = self.inmemory_signals["exif_date_tag"]
            exif_tag_count = self.inmemory_signals["exif_tag_count"]
            gps_present = self.inmemory_signals["gps_present"]
            xmp_derived = self.inmemory_signals["xmp_derived"]
            # #820 — ``.get`` (not ``[]``) for the two newest keys: an
            # inmemory_signals dict built before this field existed must still
            # produce an extract rather than a KeyError that would turn the
            # file into a HashFailure and drop it from the manifest.
            subsec_time_original = self.inmemory_signals.get("subsec_time_original")
            offset_time_original = self.inmemory_signals.get("offset_time_original")

        return MediaExtract(
            path=self.record.path,
            file_type=self.record.file_type,
            sha256=self.sha256,
            phash=self.phash,
            mean_color=self.mean_color,
            pixel_width=self.pixel_width,
            pixel_height=self.pixel_height,
            exif_date=exif_date,
            exif_date_tag=exif_date_tag,
            exif_tag_count=exif_tag_count,
            gps_present=gps_present,
            xmp_derived=xmp_derived,
            subsec_time_original=subsec_time_original,
            offset_time_original=offset_time_original,
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
    # #820 — sub-second digits and UTC offset of DateTimeOriginal, which
    # ``shot_date`` cannot carry (parse_exif_date truncates to 19 chars by
    # design). NULL on every pre-#820 row and on files whose camera wrote
    # neither tag. Text, not numeric: "05" (50 ms) must not become 5.
    subsec_time_original: Optional[str] = None
    offset_time_original: Optional[str] = None
    creation_date: Optional[str] = None  # ISO 8601 filesystem ctime
    mtime: Optional[str] = None          # ISO 8601 filesystem mtime
    group_id: Optional[str] = None       # canonical root path of connected component; written to DB
    pixel_width: Optional[int] = None    # image width in pixels; written to DB
    pixel_height: Optional[int] = None   # image height in pixels; written to DB
    phash_quality: Optional[float] = None  # 32×32 grey std-dev (recipe "5"); written to DB
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
    # rows. Persisted to ``migration_manifest.match_confidence`` (scanner/
    # manifest.py) so the review-time ``apply_best_copy`` path can honour the
    # same aggressive-delete gate as scan time, with no re-scan. It drives
    # the #517 gate: low-confidence rows are never auto-marked for deletion.
    # A UI badge is still a tracked follow-up (not exposed on the web API).
    match_confidence: Optional[str] = None


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _mean_color_distance(a: str, b: str) -> float:
    """L2 distance between two mean-color strings ("R,G,B")."""
    ra, ga, ba = (int(x) for x in a.split(","))
    rb, gb, bb = (int(x) for x in b.split(","))
    return ((ra - rb) ** 2 + (ga - gb) ** 2 + (ba - bb) ** 2) ** 0.5


def _dhash_vote(a: HashResult, b: HashResult, dhash_threshold: int) -> str:
    """#517/#524 — dHash's verdict on a pHash near-dup pair.

    Returns one of:
      * ``"agree"``    — dHash is also within ``dhash_threshold`` → a real
                         near-duplicate (group it, high confidence).
      * ``"disagree"`` — both dHashes present but beyond the threshold →
                         the pHash match is a false positive; do NOT group.
      * ``"unknown"``  — dHash unavailable on either side (no imagehash /
                         decode failure) → fall back to pHash-only and group
                         with low confidence (preserves the #517 fallback).

    pHash (DCT/frequency) and dHash (gradient/brightness) capture different
    structure, so a different-scene pair that coincidentally lands within the
    pHash threshold almost always disagrees on dHash by a wide margin
    (measured 17–32 on real false positives vs <10 for genuine near-dups).
    Requiring agreement (#524) is therefore a high-precision grouping gate
    that doesn't cost real near-duplicates.
    """
    if not a.dhash or not b.dhash or not _IMAGEHASH_AVAILABLE:
        return "unknown"
    try:
        distance = imagehash.hex_to_hash(a.dhash) - imagehash.hex_to_hash(b.dhash)
    except (ValueError, TypeError):
        return "unknown"
    return "agree" if distance <= dhash_threshold else "disagree"


def _shot_time_vote(a: HashResult, b: HashResult, tolerance_s: int = 2) -> str:
    """dedup-core research 2026-11-05 class B — do the two shot times agree?

    Returns one of:
      * ``"agree"``    — the capture times match within ``tolerance_s``, or
                         differ by a whole number of half-hours up to 14 h.
                         The half-hour case is the same instant written as UTC
                         by one tool and as local time by another (Google
                         Takeout, some cameras); on the real manifest these
                         pHash-identical pairs sit almost exactly 8 h apart and
                         are genuine copies (issue #820 has the tz background).
      * ``"disagree"`` — both EXIF dates present and the gap is neither: two
                         different captures that happen to hash alike.
      * ``"unknown"``  — either side has no EXIF date (RAW, scan, stripped
                         metadata): no evidence either way, nothing demoted.
    """
    if a.exif_date is None or b.exif_date is None:
        return "unknown"
    delta = abs((a.exif_date - b.exif_date).total_seconds())
    if delta <= tolerance_s:
        return "agree"
    if delta <= 14 * 3600 + tolerance_s and abs(delta - round(delta / 1800) * 1800) <= tolerance_s:
        return "agree"
    return "disagree"


def _screenshot_pair(a: HashResult, b: HashResult) -> bool:
    """dedup-core research 2026-11-05 class B — both files are PNG.

    PNG is the screenshot / UI-render class in a photo library. Two screenshots
    that hash alike (same app chrome, same status bar) are not one capture, so
    the pair is capped at the review tier and never earns ``high`` confidence.
    """
    return a.record.file_type == "png" and b.record.file_type == "png"


def _demotion_note(a: HashResult, b: HashResult) -> Optional[str]:
    """Short reason a pHash match is capped at ``low`` confidence, or None.

    Shot-time disagreement is named first (it carries the hour gap); a
    both-PNG pair is the fallback cause.
    """
    if _shot_time_vote(a, b) == "disagree":
        hours = abs((a.exif_date - b.exif_date).total_seconds()) / 3600
        return f"shot {hours:.1f} h apart"
    if _screenshot_pair(a, b):
        return "both screenshots"
    return None


def classify(
    records: list[HashResult],
    threshold: int = 10,
    mean_color_threshold: int = 30,
    source_priority: dict[str, int] | None = None,
    min_phash_entropy_bits: int = 4,
    dhash_threshold: int = 10,
    min_phash_dimension: int = 128,
    bktree_min_candidates: int | None = None,
    min_phash_quality: float = 16.0,
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
        dhash_threshold: Maximum dHash Hamming distance for the #524 grouping
            gate. A pHash near-dup candidate is only grouped when its dHash
            ALSO agrees within this distance (``match_confidence="high"``);
            if both dHashes are present but disagree, the pair is a pHash false
            positive and is NOT grouped. When dHash is unavailable (no
            imagehash / decode failure) the pair falls back to pHash-only and
            groups with ``match_confidence="low"``.
        min_phash_dimension: dimension gate. An image whose smaller side is
            below this many pixels is too low-detail for a 64-bit pHash to
            discriminate — tiny UI icons / thumbnails flatten to a handful of
            DCT coefficients, so unrelated small images collide and form false
            near-dup groups. Such files are excluded from pHash near-dup
            grouping (exact-SHA dedup still applies); images with unknown
            dimensions (video / decode failure) pass through. ``0`` disables.
        bktree_min_candidates: #526 — candidate count at/above which near-dup
            search uses the BK-tree instead of the brute O(N²) scan. ``None``
            (default) uses the module floor :data:`_BKTREE_MIN_CANDIDATES`;
            the scan worker passes a value measured by the #486 auto-pool
            calibration so the crossover is per-machine, not baked. Trades
            speed only — the grouping verdict is identical either way.
        min_phash_quality: pHash-quality (contrast) gate. ``hr.phash_quality``
            is the population std-dev of the 32×32 grey working image — the
            image ``imagehash.phash`` hashes. Measured on 519 real files
            (dedup-core research 2026-11-05 contrast probe): dng median 49.7,
            heic 50.3, jpg 52.7, png 43.9 (p1 8.9). The class-A frames that
            hashed to ``cccc3333cccc3333`` read 5.67 / 5.63 (two 2020 eclipse
            frames) and 15.36 (a 2023 photo of two lights in the dark), so
            16.0 removes all three from pHash grouping, plus ~1 % of real
            photos (~5 % of PNG screenshots). Such files still get exact-SHA
            dedup and the same-stem pair edge. A file below this is excluded
            from pHash near-dup grouping; ``None`` quality (video / legacy
            manifest / failure) passes. ``0`` disables the gate.
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

    # Pass 2: pHash-based (cross-format + near-duplicate). Returns the pHash
    # union-find edges (#538 — a true transitive closure, decoupled from
    # classification so a genuine near-dup is never orphaned; #824 — plus the
    # complementary edges for exact buckets that get no dedup decision).
    phash_edges = _classify_phash(
        records, rows, threshold, source_priority, mean_color_threshold,
        min_phash_entropy_bits, dhash_threshold, min_phash_dimension,
        bktree_min_candidates, min_phash_quality,
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
    pair_edges = _collect_pair_edges(records, rows, threshold)

    # Pass 5: assign group_id via union-find over duplicate_of + pair edges +
    # pHash transitive edges (#538 near-dup, #824 complementary).
    # Live Photo pair edges are certain (high); pHash edges carry their own.
    skipped = _assign_group_ids(rows, [(a, b, "high") for a, b in pair_edges] + phash_edges)
    _mark_isolated_hints(rows, skipped)

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
        # Deterministic keeper: highest source priority, then lexicographically
        # smallest path, so same-priority ties resolve identically across scans
        # (input order must not decide the keeper). Matches the lex-min
        # convention group_id / rescore rely on (#792).
        group.sort(key=lambda h: (
            _priority(h.record.source_label, source_priority),
            str(h.record.path),
        ))
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


def _phash_quality_ok(hr: HashResult, min_quality: float) -> bool:
    """True if the image has enough grey-scale contrast for its pHash to mean something.

    A flat or near-black frame downsamples to an almost uniform 32×32 grey
    image; imagehash's median split then turns noise into bits, so every such
    frame lands on a degenerate 64-bit pHash (e.g. ``cccc3333cccc3333``) and
    unrelated frames bridge into one chained group. The popcount guard (#516)
    cannot see this — the median split gives every photo popcount 32. This
    gate reads the contrast scalar directly (``HashResult.phash_quality``,
    recipe "5").

    ``min_quality <= 0`` disables the gate. ``phash_quality is None`` (video,
    legacy manifest, decode failure) passes — the same None-tolerant handling
    as the other guards. Exact-SHA dedup is never affected: this gate only
    runs in pHash candidate selection.
    """
    if min_quality <= 0:
        return True
    if hr.phash_quality is None:
        return True
    return hr.phash_quality >= min_quality


def _classify_phash(
    records: list[HashResult],
    rows: dict[str, ManifestRow],
    threshold: int,
    source_priority: dict[str, int],
    mean_color_threshold: int = 30,
    min_phash_entropy_bits: int = 4,
    dhash_threshold: int = 10,
    min_phash_dimension: int = 128,
    bktree_min_candidates: int | None = None,
    min_phash_quality: float = 16.0,
) -> list[Edge]:
    """Group by pHash; classify FORMAT_DUPLICATE and REVIEW_DUPLICATE, and
    return the pHash union-find edges — near-duplicate (#538) plus
    complementary exact-bucket (#824).

    Records are excluded from BOTH the exact-pHash format-group path and the
    near-duplicate scan when their pHash is degenerate (flat image — see
    :func:`_phash_entropy_ok`, #516) OR the image is too small for a 64-bit
    pHash to discriminate (:func:`_phash_dimension_ok` — tiny UI icons /
    thumbnails) OR the working image is too low-contrast for its pHash to
    mean anything (:func:`_phash_quality_ok`, recipe "5" — near-black /
    near-flat frames). All three classes still get exact-SHA dedup.
    """
    # Only consider records not already classified, with a valid pHash,
    # whose pHash is trustworthy (#516 entropy) and whose image is large
    # enough to discriminate (dimension gate — kills tiny-icon false groups).
    candidates = [
        hr for hr in records
        if hr.phash
        and _phash_entropy_ok(hr.phash, min_phash_entropy_bits)
        and _phash_dimension_ok(hr, min_phash_dimension)
        and _phash_quality_ok(hr, min_phash_quality)
        and str(hr.record.path) not in rows
    ]

    # Build pHash → records map (exact matches first)
    by_phash: dict[str, list[HashResult]] = {}
    for hr in candidates:
        by_phash.setdefault(hr.phash, []).append(hr)

    # Exact pHash match (hamming == 0) → FORMAT_DUPLICATE or complementary
    # RAW+lossy. Complementary buckets take no dedup decision but still return
    # their union-find edges (#824).
    exact_edges: list[Edge] = []
    for group in by_phash.values():
        if len(group) < 2:
            continue
        exact_edges.extend(_classify_format_group(
            group, rows, source_priority, mean_color_threshold, dhash_threshold
        ))

    # Near-duplicate scan: compare all pairs with hamming distance ≤ threshold.
    # Returns the union-find edges (#538 — a true transitive closure, decoupled
    # from classification).
    return exact_edges + _classify_near_duplicates(
        candidates, rows, threshold, source_priority, mean_color_threshold,
        dhash_threshold, bktree_min_candidates,
    )


def _classify_format_group(
    group: list[HashResult],
    rows: dict[str, ManifestRow],
    source_priority: dict[str, int],
    mean_color_threshold: int = 30,
    dhash_threshold: int = 10,
) -> list[Edge]:
    """Within a pHash==0 group, apply RAW+lossy exception and format priority.

    Returns the union-find edges the bucket contributes. The all-lossy path
    returns none — its ``duplicate_of`` links already carry the component.
    The two branches that take NO dedup decision return complementary edges
    instead (#824): the bucket is one shot, so it is one group, even though
    nothing in it is a duplicate to be discarded.
    """
    has_raw = any(hr.record.file_type == "raw" for hr in group)
    lossy = [hr for hr in group if hr.record.file_type in LOSSY_TYPES]

    if has_raw and lossy:
        # RAW + lossy: complementary — no dedup decision, both stay undecided
        # (''). #824: emit the union-find edge anyway. The action policy is
        # the deliberate part (cfa4f41: "never mark as FORMAT_DUPLICATE");
        # the missing edge was a side effect of a function that predated
        # group_id, and it orphaned the RAW original and its export into two
        # invisible singletons — ManifestRepository.load only yields rows with
        # a group_id. Same decoupling #88 made for Live Photo pairs and #538
        # for bridged near-dups: group together, decide independently.
        return _complementary_edges(group, mean_color_threshold, dhash_threshold)

    if len(lossy) < 2:
        # Reachable only for an ALL-RAW bucket: a video carries no pHash so it
        # is never a candidate, and every other image type with a pHash is in
        # LOSSY_TYPES — so ``has_raw`` False would imply ``len(lossy) >= 2``.
        # A CR2 and its DNG conversion hash identically with different SHAs;
        # same shot, same group, still no dedup decision (#824).
        return _complementary_edges(group, mean_color_threshold, dhash_threshold)

    # All lossy FORMAT_DUPLICATE: keep highest-format × highest-source-priority
    lossy.sort(key=lambda h: (
        FORMAT_PRIORITY.get(h.record.file_type, 99),
        _priority(h.record.source_label, source_priority),
        str(h.record.path),  # deterministic tiebreak on same format+priority (#792)
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
        # #524 — dHash grouping gate: an independent second hash must agree
        # (or be unavailable) for the format-dup to stand. "disagree" → skip.
        vote = _dhash_vote(keeper, duplicate, dhash_threshold)
        if vote == "disagree":
            continue
        # dedup-core research 2026-11-05 class B — a pHash-0 match is not a
        # certain duplicate when the shot times disagree (three captures of one
        # gate hashed identically) or when both files are PNG screenshots. The
        # duplicate_of edge still groups them; only the label and confidence
        # move to the review tier, so the aggressive auto-select skips the row.
        note = _demotion_note(keeper, duplicate)
        if note is not None:
            rows[key] = _make_row(
                duplicate,
                "REVIEW_DUPLICATE",
                duplicate_of=str(keeper.record.path),
                hamming=0,
                reason=f"same pHash as {keeper.record.path.name} but {note}",
                match_confidence="low",
            )
            continue
        rows[key] = _make_row(
            duplicate,
            "EXACT",
            duplicate_of=str(keeper.record.path),
            hamming=0,
            reason=f"format duplicate of {keeper.record.path.name} "
                   f"({duplicate.record.file_type} vs {keeper.record.file_type})",
            match_confidence="high" if vote == "agree" else "low",
        )
    # The component is already carried by the duplicate_of links written above.
    return []


def _complementary_edges(
    group: list[HashResult],
    mean_color_threshold: int,
    dhash_threshold: int,
) -> list[Edge]:
    """#824 — union-find edges for an exact-pHash bucket that takes no dedup
    decision (RAW+lossy complementary, or all-RAW). These are ``high``: the
    bucket is one shot, so the group is certain.

    A star from the lexicographically smallest member to every peer: one
    connected component, and an anchor that cannot depend on the order the
    hasher finished files in — the same lex-min rule ``_assign_group_ids``
    uses, so the rescore determinism invariant is preserved by construction.

    The same two precision gates the all-lossy path applies decide each spoke,
    because an edge asserts "these are the same shot" just as an ``EXACT`` row
    does: #462's mean-color check (skipped when either side lacks a mean color
    — RAW thumbnails routinely do) and #524's dHash vote, where a definite
    ``disagree`` means the pHash match is a false positive and there is no
    pair to draw. No row is written and no action changes.
    """
    ordered = sorted(group, key=lambda h: str(h.record.path))
    anchor = ordered[0]
    anchor_key = str(anchor.record.path)
    edges: list[Edge] = []
    for peer in ordered[1:]:
        if mean_color_threshold > 0 and anchor.mean_color and peer.mean_color:
            if _mean_color_distance(anchor.mean_color, peer.mean_color) > mean_color_threshold:
                continue
        if _dhash_vote(anchor, peer, dhash_threshold) == "disagree":
            continue
        edges.append((anchor_key, str(peer.record.path), "high"))
    return edges


# ---------------------------------------------------------------------------
# Near-duplicate candidate generation (#526)
# ---------------------------------------------------------------------------

# #526 — version token for the grouping *strategy* (candidate-search structure
# + the gates that decide a near-dup). Folded into
# ``scan_worker.hash_pool_fingerprint`` so the #486 calibration cache (which
# now also stores grouping micro-rates) invalidates when grouping changes.
# BUMP whenever the candidate-search algorithm or a grouping gate changes in a
# way that shifts cost or verdict: e.g. the BK-tree metric, the threshold
# defaults, or adding/removing a gate (mean-color #462, dHash #524, entropy
# #516, dimension #523). Opaque cache-keying token; only equality matters.
# "3" (#824): the exact tier now emits complementary edges for RAW+lossy and
# all-RAW buckets, so the grouping verdict moved.
# "4" (dedup-core research 2026-11-05 class B): shot-time and PNG-pair gates
# demote pHash matches to low confidence; edges are unchanged.
# "5": the pHash-quality (contrast) gate (min_phash_quality, 16.0) excludes
# flat/near-black frames whose degenerate pHash bridged unrelated photos
# (dedup-core research 2026-11-05 class A). Verdict moves on those files.
# "6": low-confidence edges are hints (dedup-core research 2026-11-05 §5.1 T3).
# They never merge two multi-member groups, so the torii moments that hash
# alike stay separate. A lone file attaches only to a high-cored group (one
# that holds a high edge), and two lone files may pair up; a low-only group
# never grows, so hints do not chain. A row left out of every group by a
# skipped hint becomes undecided (UNDATED without a shot date) with the
# marker "hint only (not grouped): " in its reason.
# "4" is taken by a separate branch's confidence gates; merged in order.
GROUPING_STRATEGY_VERSION = "6"

# Candidate count below which we keep the legacy O(N²) brute-force pairwise
# scan instead of building a BK-tree.
#
# Measurement (scripts/bench_grouping.py, threshold=10) found NO crossover
# where brute force wins: the BK-tree is ~5-6× faster at every N from 10 up,
# because it replaces the brute path's per-pair ``imagehash`` subtraction
# (numpy-backed, ~µs each) with an integer XOR popcount (``int.bit_count()``),
# and that constant-factor win lands *before* the quadratic term even starts
# to bite. So this threshold is NOT a performance crossover — it's only a
# guard to skip tree allocation on trivially small inputs (≤ a few dozen
# candidates, where the whole scan is sub-millisecond either way). The exact
# value is immaterial to wall-clock; 16 is a conservative floor. PR #526's
# follow-up folds an auto-calibrated structure choice into the #486
# fingerprint cache. The two paths are proven bit-identical by
# ``test_dedup.py::TestBKTreeParity`` — changing this constant only trades a
# hair of micro-overhead, never the grouping verdict.
_BKTREE_MIN_CANDIDATES = 16


class _BKTree:
    """Minimal Burkhard-Keller tree over integer perceptual hashes (#526).

    Metric is Hamming distance via XOR popcount (``int.bit_count()``,
    Python 3.10+). Each node stores one hash value plus the list of record
    indices that share it — equal hashes collapse onto a single node because
    a BK-tree cannot hold two children at edge-distance 0. Children are keyed
    by their integer edge distance to the parent.

    This is *candidate generation only*: ``query`` returns every indexed
    record within ``threshold`` Hamming of a probe, turning the O(N²)
    all-pairs near-dup scan into ~O(N log N). The grouping verdict
    (mean-color gate #462, dHash gate #524, source-priority ordering) is
    unchanged and still applied per surviving pair by the caller — the tree
    only decides *which* pairs are tested, never the outcome.
    """

    __slots__ = ("_key", "_indices", "_children")

    def __init__(self, key: int, index: int) -> None:
        self._key = key
        self._indices: list[int] = [index]
        self._children: dict[int, "_BKTree"] = {}

    def add(self, key: int, index: int) -> None:
        node = self
        while True:
            d = (node._key ^ key).bit_count()
            if d == 0:
                node._indices.append(index)
                return
            child = node._children.get(d)
            if child is None:
                node._children[d] = _BKTree(key, index)
                return
            node = child

    def query(self, key: int, threshold: int) -> list[int]:
        """Indices of every record within ``threshold`` Hamming of ``key``.

        Iterative (explicit stack) so a degenerate near-linear tree can't
        blow the recursion limit on a large library. Triangle inequality:
        a target within ``threshold`` of the probe must sit at edge distance
        in ``[d - threshold, d + threshold]`` from any node, so only those
        children are visited.
        """
        out: list[int] = []
        stack: list["_BKTree"] = [self]
        while stack:
            node = stack.pop()
            d = (node._key ^ key).bit_count()
            if d <= threshold:
                out.extend(node._indices)
            lo, hi = d - threshold, d + threshold
            for edge, child in node._children.items():
                if lo <= edge <= hi:
                    stack.append(child)
        return out


def _near_dup_neighbors(
    int_hashes: list[int], threshold: int, min_candidates: int | None = None
):
    """Return ``neighbors(i) -> iterable[int]`` yielding indices ``j > i``
    whose pHash is within ``threshold`` Hamming of record ``i`` (#526).

    Two interchangeable strategies, picked by the candidate count against
    ``min_candidates`` (``None`` → the module floor
    :data:`_BKTREE_MIN_CANDIDATES`; the scan worker passes a per-machine value
    measured by the #486 calibration):

    * brute force — ``range(i + 1, n)``; the caller's own
      ``0 < distance <= threshold`` check does the filtering, identical to
      the pre-#526 nested ``hashes[i + 1:]`` loop.
    * BK-tree — query the tree for ``i``'s neighbours, keep those with
      ``j > i``, return them sorted ascending so the caller visits pairs in
      the exact ``(i, j)`` lexicographic order the brute-force loop used.

    Both yield the same actionable ``j`` per ``i`` (the BK-tree merely skips
    the out-of-threshold ``j`` the brute body would no-op on), so the
    grouping output is bit-identical — see ``test_dedup.py::TestBKTreeParity``.
    """
    floor = _BKTREE_MIN_CANDIDATES if min_candidates is None else min_candidates
    n = len(int_hashes)
    if n < floor:
        def brute(i: int) -> range:
            return range(i + 1, n)
        return brute

    tree = _BKTree(int_hashes[0], 0)
    for idx in range(1, n):
        tree.add(int_hashes[idx], idx)

    def bk(i: int) -> list[int]:
        return sorted(j for j in tree.query(int_hashes[i], threshold) if j > i)

    return bk


def _classify_near_duplicates(
    candidates: list[HashResult],
    rows: dict[str, ManifestRow],
    threshold: int,
    source_priority: dict[str, int],
    mean_color_threshold: int = 30,
    dhash_threshold: int = 10,
    bktree_min_candidates: int | None = None,
) -> list[Edge]:
    """Flag pHash pairs with hamming distance 1–threshold as REVIEW_DUPLICATE,
    and return their ``(path_a, path_b, confidence)`` union-find edges.

    #538 — edge collection is DECOUPLED from classification. Every pair that
    passes the distance + mean-color (#462) + dHash (#524) gates yields a
    union-find edge, even when one endpoint is already classified via another
    bridge. Previously the ``if hr_b … in rows: continue`` guard dropped such an
    edge, so a genuine near-dup whose only bridge was already classified fell to
    ``group_id=None`` (an order-dependent spanning *forest*, not a true
    transitive closure). Classification (which row becomes REVIEW_DUPLICATE, and
    its ``match_confidence``) is unchanged — only the returned edge set is new.
    """
    if not _IMAGEHASH_AVAILABLE:
        return []

    # GROUPING 6: iterate in path order, so the first pair that flags a row (and
    # therefore its duplicate_of / reason) does not depend on record order.
    unclassified = sorted(
        (hr for hr in candidates if str(hr.record.path) not in rows),
        key=lambda hr: str(hr.record.path),
    )
    hashes = [(hr, imagehash.hex_to_hash(hr.phash)) for hr in unclassified if hr.phash]

    # #526 — candidate generation via BK-tree (large N) or brute force (small
    # N), replacing the inner ``hashes[i + 1:]`` enumeration ONLY. Every
    # per-pair gate below is unchanged, so the output is bit-identical to the
    # pre-#526 O(N²) scan (proven by TestBKTreeParity). The int form feeds the
    # tree's fast XOR-popcount metric; the authoritative recorded distance is
    # still ``hash_a - hash_b`` (imagehash) on the surviving candidates.
    int_hashes = [int(hr.phash, 16) for hr, _ in hashes]
    neighbors = _near_dup_neighbors(int_hashes, threshold, bktree_min_candidates)

    edges: list[Edge] = []
    for i, (hr_a, hash_a) in enumerate(hashes):
        # hr_a may already be classified — it still serves as a comparator so
        # transitively-similar files connect into one component.
        for j in neighbors(i):
            hr_b, hash_b = hashes[j]
            distance = hash_a - hash_b
            if not (0 < distance <= threshold):
                continue
            # Mean-color gate: reject if average colors clearly differ (a pHash
            # false positive — similar DCT structure, different colours).
            # Skipped when either file lacks mean_color (RAW, hash failure).
            if mean_color_threshold > 0 and hr_a.mean_color and hr_b.mean_color:
                if _mean_color_distance(hr_a.mean_color, hr_b.mean_color) > mean_color_threshold:
                    continue
            # #524 — dHash grouping gate: pHash is only a candidate; the
            # independent dHash must also agree (or be unavailable). A
            # "disagree" verdict means the two scenes only collided on the
            # coarse pHash → not a real near-dup → no edge, no classification.
            vote = _dhash_vote(hr_a, hr_b, dhash_threshold)
            if vote == "disagree":
                continue
            # #538 — record the union-find edge for EVERY surviving pair,
            # decoupled from the in-rows classification guard below. This is the
            # only behavioural change: the edge stands even when hr_b is already
            # classified (exactly the case the old top-of-loop guard dropped →
            # orphan). Redundant edges are union-find no-ops.
            # dedup-core research 2026-11-05 class B — shot-time or PNG-pair
            # demotion caps confidence at "low" whatever the dHash said. The
            # edge and the row share this one verdict.
            note = _demotion_note(hr_a, hr_b)
            confidence = _edge_confidence(vote, note)
            edges.append((str(hr_a.record.path), str(hr_b.record.path), confidence))
            # Classification (unchanged from pre-#538): flag the lower-priority
            # side, but only when hr_b is still unclassified — so the
            # REVIEW_DUPLICATE / match_confidence assignment is byte-identical.
            if str(hr_b.record.path) in rows:
                continue
            ordered = sorted(
                [hr_a, hr_b],
                key=lambda h: (
                    _priority(h.record.source_label, source_priority),
                    str(h.record.path),  # deterministic on same-priority (#792)
                ),
            )
            flagged = ordered[1]
            flagged_key = str(flagged.record.path)
            if flagged_key not in rows:
                suffix = f"; {note}" if note is not None else ""
                rows[flagged_key] = _make_row(
                    flagged,
                    "REVIEW_DUPLICATE",
                    duplicate_of=str(ordered[0].record.path),
                    hamming=distance,
                    reason=f"near-duplicate (hamming={distance}) of "
                           f"{ordered[0].record.path.name}{suffix}",
                    match_confidence=confidence,
                )
    return edges


def _edge_confidence(vote: str, note: Optional[str]) -> str:
    """Confidence of a pHash near-dup pair: ``high`` only when dHash agrees
    and no demotion note (shot-time / both-PNG, class B) applies."""
    return "high" if vote == "agree" and note is None else "low"


def _collect_pair_edges(
    records: list[HashResult], rows: dict[str, ManifestRow], threshold: int = 10
) -> list[tuple[str, str]]:
    """Return ``(own_path, peer_path)`` edges for every Live Photo cluster
    member that survived classification.

    #539 — same-capture gate. The cluster comes from the walker keyed on
    filename only (``(clean_stem, number)`` + directory); it assumes
    "same stem ⇒ same capture", which is false when unrelated files collide
    on a name (merged folders, multi-camera, Takeout). To stop a name
    collision from unioning genuinely-different photos, an edge is dropped
    when BOTH members carry a pHash AND their Hamming distance exceeds
    ``threshold`` — positive evidence they are different images. The gate is
    deliberately conservative: it fires only on disconfirming content
    evidence, so any pair where a side lacks a pHash (a Live Photo's MOV/MP4)
    or whose pHashes agree (a RAW+JPG / HEIC+JPG of one shot) is preserved
    exactly as before. A pHash gate is used rather than a shot-date gate
    because a Live Photo's HEIC (``DateTimeOriginal``, camera-local) and its
    MOV (QuickTime date, frequently UTC) legitimately differ by a whole
    timezone offset — a date gate would split real pairs.

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
    by_path: dict[str, HashResult] = {str(hr.record.path): hr for hr in records}
    for hr in records:
        own_key = str(hr.record.path)
        if own_key not in rows:
            continue
        for peer_path in hr.record.pair_cluster:
            peer_key = str(peer_path)
            if peer_key not in rows:
                continue
            # #539 — drop the edge only on positive evidence the two
            # same-stem files are different images. ``hamming_distance``
            # returns None when either pHash is missing (a video peer) or
            # imagehash is unavailable; None means "no evidence" → keep the
            # edge, preserving Live Photo and same-shot RAW+JPG pairings.
            peer_hr = by_path.get(peer_key)
            if peer_hr is not None:
                d = hamming_distance(hr.phash, peer_hr.phash)
                if d is not None and d > threshold:
                    continue
            edges.append((own_key, peer_key))
    return edges


def _row_edges(rows: dict[str, ManifestRow]) -> list[Edge]:
    """``duplicate_of`` links as edges. A link's confidence is its row's
    ``match_confidence``: a ``low`` link is a hint, everything else is high."""
    return [
        (row.source_path, row.duplicate_of,
         "low" if row.match_confidence == "low" else "high")
        for row in rows.values() if row.duplicate_of
    ]


HINT_ONLY_PREFIX = "hint only (not grouped): "


def _mark_isolated_hints(
    rows: dict[str, ManifestRow], skipped: list[tuple[str, str]]
) -> None:
    """GROUPING 6 — a row left out of every group by a skipped low hint must
    not carry a dedup action: the review UI groups rows by ``group_id``, and a
    REVIEW row with no group would be a duplicate of nobody. A flagged row goes
    back to undecided (UNDATED when it has no shot date, as in Pass 3) and keeps
    its ``duplicate_of``. A row that was never flagged (its hint was the
    partner's link) gets the partner as ``duplicate_of`` and ``low`` confidence,
    so no lone file is silently orphaned. Every such reason carries the marker.
    A row still in a group through another edge is untouched."""
    partner_of: dict[str, str] = {}
    for a, b in skipped:
        partner_of.setdefault(a, b)
        partner_of.setdefault(b, a)
    for row in rows.values():
        if row.group_id is not None:
            continue
        if row.duplicate_of:
            row.action = "" if row.shot_date is not None else "UNDATED"
            row.reason = HINT_ONLY_PREFIX + row.reason
        elif row.source_path in partner_of:
            partner = partner_of[row.source_path]
            row.duplicate_of = partner
            row.match_confidence = "low"
            row.reason = HINT_ONLY_PREFIX + f"low-confidence match with {Path(partner).name}"


def _hint_may_join(
    a: str,
    b: str,
    find: Callable[[str], str],
    joined: set[str],
    core_roots: set[str],
) -> bool:
    """Whether a low hint ``a``-``b`` may union. Two lone files pair up. A
    lone file attaches only to a high-cored component (one holding a high
    edge); a low-only component never grows, so hints cannot chain."""
    a_lone, b_lone = a not in joined, b not in joined
    if a_lone and b_lone:
        return True
    if a_lone:
        return find(b) in core_roots
    if b_lone:
        return find(a) in core_roots
    return False  # a hint never merges two multi-member groups


def _assign_group_ids(
    rows: dict[str, ManifestRow],
    edges: list[Edge] | None = None,
) -> list[tuple[str, str]]:
    """Assign group_id via union-find over duplicate_of edges + ``edges``.

    Files transitively connected by ``high`` edges (A→B, B→C) all receive the
    same group_id — the lexicographically smallest source_path in the
    component. Isolated files (no edge at all) receive group_id = None.

    ``edges`` carries the non-``duplicate_of`` union edges: Live Photo
    HEIC ↔ MOV/MP4 pairs (photo-manager#88, always high) and the pHash
    near-duplicate and complementary edges (#538 / #824), each with its own
    confidence.

    Low-confidence edges (``GROUPING_STRATEGY_VERSION`` "6") are hints. They
    are processed after every high edge, in sorted order (deterministic, so
    the result never depends on record order): a low edge whose endpoints are
    already in the same component is a no-op; two lone files pair up; a lone
    file attaches to a component only when that component is high-cored (holds
    a high edge); everything else is skipped, so a hint never merges two groups
    and a low-only pair never grows. A file whose only edges were skipped stays
    ``group_id=None``, and the hint is still visible in its ``reason`` and
    ``duplicate_of``.
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

    all_edges = _row_edges(rows) + list(edges or [])
    # Nodes in a component of two or more members. Every node joined by a
    # union is one, so membership is exactly "not a lone singleton".
    joined: set[str] = set()
    # Roots of components that hold at least one high edge ("high-cored").
    core_roots: set[str] = set()

    def join(a: str, b: str, core: bool) -> None:
        ra, rb = find(a), find(b)
        if ra == rb:
            if core:
                core_roots.add(ra)
            return
        was_core = core or ra in core_roots or rb in core_roots
        union(a, b)
        core_roots.difference_update({ra, rb})
        if was_core:
            core_roots.add(find(a))
        joined.update((a, b))

    for a, b, confidence in all_edges:
        if confidence == "high":
            join(a, b, core=True)

    # Normalise orientation and sort: the hint pass must not depend on which
    # side of a pair the classifier happened to write first.
    low_pairs = sorted(
        (min(a, b), max(a, b)) for a, b, confidence in all_edges if confidence == "low"
    )
    skipped: list[tuple[str, str]] = []
    for a, b in low_pairs:
        if find(a) == find(b):
            continue
        if _hint_may_join(a, b, find, joined, core_roots):
            join(a, b, core=False)
        else:
            skipped.append((a, b))

    for row in rows.values():
        if row.source_path in joined:
            row.group_id = find(row.source_path)
        # else: stays None (isolated file, or a hint that was not taken)
    return skipped


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
        phash_quality=hr.phash_quality,
        match_confidence=match_confidence,
    )
