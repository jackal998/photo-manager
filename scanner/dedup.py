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

from scanner.media import RAW_EXTENSIONS, is_video
from scanner.walker import FileRecord

# ---------------------------------------------------------------------------
# Priority tables
# ---------------------------------------------------------------------------

FORMAT_PRIORITY = {"heic": 0, "jpeg": 1, "png": 2, "gif": 3, "webp": 4, "raw": -1}
# raw is intentionally -1 (not comparable with lossy — RAW+lossy always co-exist)

LOSSY_TYPES = {"jpeg", "heic", "png", "gif", "webp"}

# Same-moment burst gap (dedup-core research 2026-11-06, T2). Two shot times
# within this many seconds "agree" (``_shot_time_vote``), and every gap between
# consecutive frames of a burst must sit within it too (``_is_burst``). One
# constant, so the two cannot drift apart.
BURST_MAX_GAP_S = 2

# A union-find edge: (path_a, path_b, confidence), confidence "high" or "pair".
# "high" edges merge groups. "pair" edges are a Live Photo image<->video
# same-stem link: they join a group only when another edge already grouped one
# of the pair's members, so a lone pair never forms a group (GROUPING "9"; see
# ``_pair_pass``). Low-confidence evidence is never an edge: it is a ``Hint``
# (GROUPING "10"), which is only ever shown as text (``_mark_hints``).
Edge = tuple[str, str, str]

# A low-confidence match, (path_a, path_b, detail). It never groups anything;
# ``_mark_hints`` names it in the reason of both files.
Hint = tuple[str, str, str]

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
    # Scan-time only, never persisted to the manifest: the 32×32 RGB pixel
    # signature (3,072 bytes) of the EXACT pixel-identity gate
    # (``_pixels_identical``). None for video, hash failure, and any
    # HashResult rebuilt without it; a None on either side demotes EXACT.
    rgb32: Optional[bytes] = None
    # GROUPING 10 (G3): Apple ContentIdentifier, upper-cased, backfilled from
    # the exiftool extract in scan_runner. Persisted (``content_identifier``
    # column). None for stills without an Apple MakerNote and for non-Apple files.
    content_identifier: Optional[str] = None

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
    # GROUPING 10 (G3): Apple ContentIdentifier (upper-cased), persisted so the
    # pairing evidence survives a rescan-free review. Nothing reads it at load.
    content_identifier: Optional[str] = None


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


def _shot_time_vote(a: HashResult, b: HashResult, tolerance_s: int = BURST_MAX_GAP_S) -> str:
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
    return _time_vote(a.exif_date, b.exif_date, tolerance_s)


def _time_vote(
    a_dt: datetime | None, b_dt: datetime | None, tolerance_s: int = BURST_MAX_GAP_S
) -> str:
    """``_shot_time_vote`` on two bare shot times: the same three answers and the
    same tolerance. The union rule (``_would_link_two_moments``) needs it for
    dated rows that are not HashResults."""
    if a_dt is None or b_dt is None:
        return "unknown"
    delta = abs((a_dt - b_dt).total_seconds())
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


def _gap_text(seconds: float) -> str:
    """A shot-time gap for a hint: seconds under a minute, minutes under an
    hour, hours otherwise. A gap of seconds must not print as "0.0 h"."""
    if seconds < 60:
        return f"{seconds:.0f} s"
    if seconds < 3600:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


def _demotion_note(
    a: HashResult, b: HashResult, screenshot_demotion: bool = True
) -> Optional[str]:
    """Short reason a pHash match is capped at ``low`` confidence, or None.

    A non-None note makes the match a hint, which never groups (GROUPING "10").
    Shot-time disagreement is named first (it carries the hour gap); a
    both-PNG pair is the fallback cause, and only while ``screenshot_demotion``
    (the PR-4a B7 rule) is on.
    """
    if _shot_time_vote(a, b) == "disagree":
        gap = _gap_text(abs((a.exif_date - b.exif_date).total_seconds()))
        return f"shot {gap} apart"
    if screenshot_demotion and _screenshot_pair(a, b):
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
    burst_stacks: bool = True,
    screenshot_demotion: bool = True,
) -> list[ManifestRow]:
    """Assign an action to every record and return ManifestRows.

    ``burst_stacks`` (PR-4a B5) and ``screenshot_demotion`` (PR-4a B7) default
    to the grouping-9 behaviour; the scan passes the user's rule toggles.

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
            imagehash / decode failure) the pair is a low-confidence hint:
            it never groups (GROUPING "10"), and both files name it in their
            reason.
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
            dedup and the Live Photo pair edge. A file below this is excluded
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
    phash_edges, phash_hints = _classify_phash(
        records, rows, threshold, source_priority, mean_color_threshold,
        min_phash_entropy_bits, dhash_threshold, min_phash_dimension,
        bktree_min_candidates, min_phash_quality,
        screenshot_demotion=screenshot_demotion,
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
    # pair edges. Each row keeps its own action / user_decision — the image's
    # classification no longer dictates the video's. GROUPING "10": only a
    # Live video whose ContentIdentifier equals its still's yields an edge.
    pair_edges = _collect_pair_edges(records, rows)

    # Pass 5: assign group_id via union-find over duplicate_of + pair edges +
    # pHash transitive edges (#538 near-dup, #824 complementary). A pair
    # cluster that would bridge two groups is skipped and says so in its reason.
    # A content edge that would link two shot moments through an undated row
    # comes back as a hint (GROUPING 10, iteration 3).
    skipped_pairs, undated_hints = _assign_group_ids(rows, pair_edges + phash_edges)
    for path in skipped_pairs:
        rows[path].reason += _PAIR_SKIPPED_SUFFIX
    if burst_stacks:
        _mark_burst_stacks(rows)
    # GROUPING 10: hints run last, so a burst reason stays first in the text.
    _mark_hints(rows, [*phash_hints, *undated_hints])

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
    screenshot_demotion: bool = True,
) -> tuple[list[Edge], list[Hint]]:
    """Group by pHash; classify FORMAT_DUPLICATE and REVIEW_DUPLICATE, and
    return the pHash union-find edges — near-duplicate (#538) plus
    complementary exact-bucket (#824) — and the low-confidence hints (GROUPING
    "10") that did not earn an edge.

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
    hints: list[Hint] = []
    for group in by_phash.values():
        if len(group) < 2:
            continue
        group_edges, group_hints = _classify_format_group(
            group, rows, source_priority, mean_color_threshold, dhash_threshold,
            screenshot_demotion=screenshot_demotion,
        )
        exact_edges.extend(group_edges)
        hints.extend(group_hints)

    # Near-duplicate scan: compare all pairs with hamming distance ≤ threshold.
    # Returns the union-find edges (#538 — a true transitive closure, decoupled
    # from classification).
    near_edges, near_hints = _classify_near_duplicates(
        candidates, rows, threshold, source_priority, mean_color_threshold,
        dhash_threshold, bktree_min_candidates,
        screenshot_demotion=screenshot_demotion,
    )
    return exact_edges + near_edges, hints + near_hints


# Pixel-identity gate for an EXACT format duplicate (pHash 0, different bytes).
# Two conditions on the 32×32 RGB signatures, both required:
#  1. mean absolute difference per byte (0-255) <= FORMAT_DUP_MAX_RGB_DIFF.
#  2. max per-channel mean shift (|mean R/G/B of a - of b|, over the 1,024
#     pixels) <= FORMAT_DUP_MAX_CHANNEL_SHIFT. Condition 1 alone admits a
#     single-channel edit (+10 on R is a mean diff of 3.3); condition 2 rejects it.
# Calibrated on the dev test set (docs/audits/testset-2026-11/probe_exact_colour.py,
# 85 pairs): condition 1 alone passes IMG_0405/0406 (a true edit), whose max
# channel shift is 5.72, so condition 2 demotes it; no honest re-encode exceeds
# a channel shift of 2.07.
FORMAT_DUP_MAX_RGB_DIFF = 6.0
FORMAT_DUP_MAX_CHANNEL_SHIFT = 4.0


def _channel_means(sig: bytes) -> tuple[float, float, float]:
    """Mean of the R, G and B bytes over the signature's pixels."""
    n = len(sig) // 3
    return tuple(sum(sig[c::3]) / n for c in range(3))


def _pixels_identical(a: HashResult, b: HashResult) -> Optional[bool]:
    """Pixel-identity verdict for two pHash-0 members: ``None`` when either
    signature is missing (cannot verify). ``False`` when the pixel dimensions
    differ, unless they are a 90° swap: ``pixel_width/height`` are read BEFORE
    EXIF orientation is applied while the signature is taken AFTER it, so an
    Orientation=6 original and its rotation-baked export differ by a swap. The
    signature itself still rejects a genuinely different image. Else both
    gates: the mean absolute byte difference and the channel-shift gate."""
    if a.rgb32 is None or b.rgb32 is None:
        return None
    dims_a = (a.pixel_width, a.pixel_height)
    dims_b = (b.pixel_width, b.pixel_height)
    if dims_a != dims_b and dims_a != (dims_b[1], dims_b[0]):
        return False
    diff = sum(abs(x - y) for x, y in zip(a.rgb32, b.rgb32)) / len(a.rgb32)
    if diff > FORMAT_DUP_MAX_RGB_DIFF:
        return False
    shift = max(
        abs(x - y) for x, y in zip(_channel_means(a.rgb32), _channel_means(b.rgb32))
    )
    return shift <= FORMAT_DUP_MAX_CHANNEL_SHIFT


def _format_review_row(
    duplicate: HashResult, keeper: HashResult, reason: str
) -> ManifestRow:
    """A format duplicate that the pixel gate will not call EXACT: same group
    (``duplicate_of`` the keeper), REVIEW_DUPLICATE, ``high`` confidence. It is
    only reached when the dHash agrees, or the pixels are known to be equal."""
    return _make_row(
        duplicate,
        "REVIEW_DUPLICATE",
        duplicate_of=str(keeper.record.path),
        hamming=0,
        reason=reason,
        match_confidence="high",
    )


def _hint(a: HashResult, b: HashResult, detail: str) -> Hint:
    """A low-confidence format or shot-time match, as a hint (GROUPING "10")."""
    return (str(a.record.path), str(b.record.path), detail)


def _keeper_key(h: HashResult, source_priority: dict[str, int]) -> tuple:
    """The keeper rule of an all-lossy bucket: highest format, then highest
    source priority, then lexicographically smallest path (#792)."""
    return (
        FORMAT_PRIORITY.get(h.record.file_type, 99),
        _priority(h.record.source_label, source_priority),
        str(h.record.path),
    )


def _shot_moments(lossy: list[HashResult]) -> list[list[HashResult]]:
    """GROUPING "10" — split an all-lossy pHash-0 bucket into shot moments.

    Dated members link when their shot times agree (``_shot_time_vote``),
    transitively: the same chaining the same-moment burst rule uses, so a burst
    of frames two seconds apart is one moment. Undated members follow
    ``_complementary_edges``: after the dated members, each joins the first
    moment it agrees with (an unknown time agrees with anything), so an undated
    file never links two moments.
    """
    moments: list[list[HashResult]] = []
    dated = sorted(
        (h for h in lossy if h.exif_date is not None), key=lambda h: str(h.record.path)
    )
    for member in dated:
        merged = [member]
        rest: list[list[HashResult]] = []
        for moment in moments:
            if any(_shot_time_vote(member, other) != "disagree" for other in moment):
                merged.extend(moment)
            else:
                rest.append(moment)
        moments = rest + [merged]
    undated = sorted(
        (h for h in lossy if h.exif_date is None), key=lambda h: str(h.record.path)
    )
    for member in undated:
        for moment in moments:
            if any(_shot_time_vote(member, other) != "disagree" for other in moment):
                moment.append(member)
                break
        else:
            moments.append([member])
    return moments


def _format_verdict(
    keeper: HashResult,
    duplicate: HashResult,
    mean_color_threshold: int,
    dhash_threshold: int,
    screenshot_demotion: bool,
    same_moment: bool = False,
) -> ManifestRow | Hint | None:
    """The verdict for one lossy duplicate of a pHash-0 keeper: a row when the
    match is certain enough to group, a ``Hint`` when it is not, or ``None``
    when a gate says the pair is not a duplicate at all.

    Two independent signals make a match ``high`` (GROUPING "10", Principle A):
    the dHash vote agrees, or the 32×32 pixel signatures are identical. A
    demotion note (shot time disagrees, or a PNG pair) and an unknown second
    signal are hints. EXACT still means pixel-identical (GROUPING "8").

    ``same_moment``: the duplicate was clustered into the keeper's shot moment
    (``_shot_moments``), which already is the shot-time agreement, so only the
    PNG rule can demote the pair."""
    if not _gates_pass(keeper, duplicate, mean_color_threshold, dhash_threshold):
        return None
    vote = _dhash_vote(keeper, duplicate, dhash_threshold)
    # dedup-core research 2026-11-05 class B — a pHash-0 match is not a certain
    # duplicate when the shot times disagree or both files are PNG screenshots.
    if same_moment:
        note = "both screenshots" if screenshot_demotion and _screenshot_pair(
            keeper, duplicate) else None
    else:
        note = _demotion_note(keeper, duplicate, screenshot_demotion)
    if note is not None:
        return _hint(keeper, duplicate, f"same pHash, {note}")
    # EXACT means pixel-identical, not just pHash 0: pHash is greyscale, so
    # a colour grade, a resize or the next frame of a burst also hashes 0.
    same_pixels = _pixels_identical(keeper, duplicate)
    if vote != "agree" and same_pixels is not True:
        detail = "same pHash, pixels differ" if same_pixels is False else (
            "same pHash, pixel check unavailable"
        )
        return _hint(keeper, duplicate, detail + ", dHash unavailable")
    if same_pixels is False:
        return _format_review_row(
            duplicate, keeper,
            "same pHash, pixels differ (edited, resized or another shot)",
        )
    if same_pixels is None:
        return _format_review_row(
            duplicate, keeper, "same pHash, pixel check unavailable",
        )
    return _make_row(
        duplicate,
        "EXACT",
        duplicate_of=str(keeper.record.path),
        hamming=0,
        reason=f"format duplicate of {keeper.record.path.name} "
               f"({duplicate.record.file_type} vs {keeper.record.file_type})",
        match_confidence="high",
    )


def _classify_format_group(
    group: list[HashResult],
    rows: dict[str, ManifestRow],
    source_priority: dict[str, int],
    mean_color_threshold: int = 30,
    dhash_threshold: int = 10,
    screenshot_demotion: bool = True,
) -> tuple[list[Edge], list[Hint]]:
    """Within a pHash==0 group, apply RAW+lossy exception and format priority.

    Returns ``(edges, hints)`` the bucket contributes. The all-lossy path
    returns no edges — its ``duplicate_of`` links already carry the component —
    but a low-confidence duplicate becomes a hint (GROUPING "10"). The two
    branches that take NO dedup decision return complementary edges instead
    (#824): the bucket is one shot, so it is one group, even though nothing in
    it is a duplicate to be discarded.
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
        return _complementary_edges(
            group, mean_color_threshold, dhash_threshold, screenshot_demotion
        )

    if len(lossy) < 2:
        # Reachable only for an ALL-RAW bucket: a video carries no pHash so it
        # is never a candidate, and every other image type with a pHash is in
        # LOSSY_TYPES — so ``has_raw`` False would imply ``len(lossy) >= 2``.
        # A CR2 and its DNG conversion hash identically with different SHAs;
        # same shot, same group, still no dedup decision (#824).
        return _complementary_edges(
            group, mean_color_threshold, dhash_threshold, screenshot_demotion
        )

    # All lossy FORMAT_DUPLICATE. GROUPING "10": one keeper per shot moment, so
    # a second moment's genuine pair is compared to its own keeper, not lost.
    # Moments are ordered by their keeper, so the result is deterministic.
    moments = sorted(
        (sorted(m, key=lambda h: _keeper_key(h, source_priority))
         for m in _shot_moments(lossy)),
        key=lambda ordered: _keeper_key(ordered[0], source_priority),
    )
    hints: list[Hint] = []
    for ordered in moments:
        keeper = ordered[0]
        for duplicate in ordered[1:]:
            key = str(duplicate.record.path)
            if key in rows:
                continue
            verdict = _format_verdict(
                keeper, duplicate, mean_color_threshold, dhash_threshold,
                screenshot_demotion, same_moment=True,
            )
            if isinstance(verdict, ManifestRow):
                rows[key] = verdict
            elif verdict is not None:
                hints.append(verdict)
    # Cross-moment links: each later moment's keeper hints at the first keeper.
    # Moments disagree pairwise on shot time, so the note is set; an unknown
    # time (undated keeper) yields no note and no link.
    first = moments[0][0]
    for ordered in moments[1:]:
        note = _demotion_note(first, ordered[0], screenshot_demotion)
        if note is not None:
            hints.append(_hint(first, ordered[0], f"same pHash, {note}"))
    # The component is already carried by the duplicate_of links written above.
    return [], hints


def _complementary_edges(
    group: list[HashResult],
    mean_color_threshold: int,
    dhash_threshold: int,
    screenshot_demotion: bool = True,
) -> tuple[list[Edge], list[Hint]]:
    """#824 — union-find edges for an exact-pHash bucket that takes no dedup
    decision (RAW+lossy complementary, or all-RAW). The bucket is split by shot
    time, the same way the all-lossy and near-dup paths decide a pair.

    Members are visited in path order, dated before undated. Each joins the first cluster
    whose anchor it agrees with on shot time (``_demotion_note`` is None; an
    unknown time counts as agree, as before), else it starts a new cluster.
    A member is tied to its cluster anchor by a ``high`` edge. Between clusters
    a later anchor is hinted to the first anchor (GROUPING "10"): disagreeing
    captures are only a hint, never a certain group.

    Both gates decide each edge, because an edge asserts "same shot": #462's
    mean-colour check (skipped when either side lacks a mean colour) and #524's
    dHash vote, where a definite ``disagree`` means no pair to draw. No row is
    written and no action changes.
    """
    # Dated members first: an undated member (unknown time, so it "agrees" with
    # anything) must never become an anchor while a dated member exists, or it
    # would pull every moment's dated members into its cluster.
    ordered = sorted(group, key=lambda h: (h.exif_date is None, str(h.record.path)))
    anchors: list[HashResult] = []
    edges: list[Edge] = []
    hints: list[Hint] = []
    for member in ordered:
        anchor = next(
            (
                a for a in anchors
                if _demotion_note(a, member, screenshot_demotion) is None
            ),
            None,
        )
        if anchor is None:
            anchors.append(member)
        elif _gates_pass(anchor, member, mean_color_threshold, dhash_threshold):
            edges.append((str(anchor.record.path), str(member.record.path), "high"))
    # Every anchor after the first disagrees with the first on shot time or
    # PNG-pair status, so its note is never None here.
    for other in anchors[1:]:
        note = _demotion_note(anchors[0], other, screenshot_demotion)
        if _gates_pass(anchors[0], other, mean_color_threshold, dhash_threshold):
            hints.append(_hint(anchors[0], other, f"same pHash, {note}"))
    return edges, hints


def _gates_pass(
    a: HashResult,
    b: HashResult,
    mean_color_threshold: int,
    dhash_threshold: int,
) -> bool:
    """#462 mean-colour and #524 dHash gates for one exact-bucket edge."""
    if mean_color_threshold > 0 and a.mean_color and b.mean_color:
        if _mean_color_distance(a.mean_color, b.mean_color) > mean_color_threshold:
            return False
    return _dhash_vote(a, b, dhash_threshold) != "disagree"


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
# "7": a same-moment burst (consecutive frames <= BURST_MAX_GAP_S apart, span
# longer than that) becomes a keep-all stack: no frame is a delete candidate
# (``_mark_burst_stacks``). Relabels only; no edge is added or removed.
# "4" is taken by a separate branch's confidence gates; merged in order.
# "8": EXACT for a pHash-0 match with different bytes now requires pixel
# identity (``_pixels_identical``: equal or rotation-swapped dimensions, 32×32
# RGB mean difference <= FORMAT_DUP_MAX_RGB_DIFF, max channel shift <=
# FORMAT_DUP_MAX_CHANNEL_SHIFT). Otherwise the row is REVIEW_DUPLICATE, at the
# confidence of the dHash vote. Edges are unchanged except that a dHash-unknown
# format duplicate keeps its low edge.
# "9": an image<->video same-stem pair is a "pair" edge (``_pair_pass``). It
# shares a group_id only when its still is grouped for another reason; a lone
# Live Photo pair gets group_id None. Image<->image pairs are unchanged.
# "10": content-gated identity, step 1 (dedup-core research 2026-11-05, tiered
# identity). Low-confidence evidence never unions: a low match is a hint
# (``_mark_hints``) that names its partner in the reason and never groups or
# sets an action. Two signals make a pHash match high: dHash agreement, or
# identical 32x32 pixels (G6). Two PNGs group only as byte copies while B7 is
# on. Stem and pair edges are unchanged in this version (iteration 2).
# Iteration 3: an undated row never links two shot moments through a content
# edge either (``_assign_group_ids``); the refused edge is a hint.
GROUPING_STRATEGY_VERSION = "10"

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
    screenshot_demotion: bool = True,
) -> tuple[list[Edge], list[Hint]]:
    """Flag pHash pairs with hamming distance 1–threshold as REVIEW_DUPLICATE,
    and return their ``(edges, hints)``.

    #538 — edge collection is DECOUPLED from classification. Every pair that
    passes the distance + mean-color (#462) + dHash (#524) gates and is
    ``high`` yields a union-find edge, even when one endpoint is already
    classified via another bridge. Previously the ``if hr_b … in rows: continue``
    guard dropped such an edge, so a genuine near-dup whose only bridge was
    already classified fell to ``group_id=None`` (an order-dependent spanning
    *forest*, not a true transitive closure).

    GROUPING "10" — a ``low`` pair is a hint: it writes no row and no edge
    (Principle A). Only ``high`` pairs classify, so a hint never claims a row
    that a later high pair could have flagged.
    """
    if not _IMAGEHASH_AVAILABLE:
        return [], []

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
    hints: list[Hint] = []
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
            # dedup-core research 2026-11-05 class B — shot-time or PNG-pair
            # demotion caps confidence at "low" whatever the dHash said.
            note = _demotion_note(hr_a, hr_b, screenshot_demotion)
            confidence = _edge_confidence(vote, note)
            if confidence == "low":
                detail = f"hamming={distance}"
                detail += f", {note}" if note is not None else ", dHash unavailable"
                hints.append((str(hr_a.record.path), str(hr_b.record.path), detail))
                continue
            # #538 — record the union-find edge for EVERY surviving high pair,
            # decoupled from the in-rows classification guard below. The edge
            # stands even when hr_b is already classified (exactly the case the
            # old top-of-loop guard dropped → orphan). Redundant edges are
            # union-find no-ops.
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
                rows[flagged_key] = _make_row(
                    flagged,
                    "REVIEW_DUPLICATE",
                    duplicate_of=str(ordered[0].record.path),
                    hamming=distance,
                    reason=f"near-duplicate (hamming={distance}) of "
                           f"{ordered[0].record.path.name}",
                    match_confidence=confidence,
                )
    return edges, hints


def _edge_confidence(vote: str, note: Optional[str]) -> str:
    """Confidence of a pHash near-dup pair: ``high`` only when dHash agrees
    and no demotion note (shot-time / both-PNG, class B) applies."""
    return "high" if vote == "agree" and note is None else "low"


def _collect_pair_edges(
    records: list[HashResult], rows: dict[str, ManifestRow]
) -> list[Edge]:
    """Return the Live Photo "pair" edges between same-stem cluster members.

    GROUPING "10" (G4, owner decision 2026-11-07): a filename never creates an
    edge. The walker's ``pair_cluster`` is keyed on the name alone, so it
    proves nothing about capture. Two rules replace the stem edge:

    * still <-> still: no edge. Stills group only through the pHash tiers,
      which compare every candidate pair with content evidence anyway.
    * any pair with a video side: a ``"pair"`` edge only when both files carry
      the same non-None Apple ContentIdentifier. A video whose identity is
      missing or different is not its still's passenger.

    Action, ``user_decision`` and ``reason`` are never propagated. Each row keeps
    its own classification. A pair edge only links rows into a group, and
    ``_pair_pass`` decides whether that link may join a group at all.
    """
    edges: list[Edge] = []
    by_path: dict[str, HashResult] = {str(hr.record.path): hr for hr in records}
    for hr in records:
        own_key = str(hr.record.path)
        if own_key not in rows:
            continue
        for peer_path in hr.record.pair_cluster:
            peer_key = str(peer_path)
            peer_hr = by_path.get(peer_key)
            if peer_key not in rows or peer_hr is None:
                continue
            if not (is_video(own_key) or is_video(peer_key)):
                continue  # still <-> still: content tiers only (G4)
            cid = hr.content_identifier
            if cid is None or peer_hr.content_identifier != cid:
                continue  # no device-written identity shared: not a pair
            edges.append((own_key, peer_key, "pair"))
    return edges


def _row_edges(rows: dict[str, ManifestRow]) -> list[Edge]:
    """``duplicate_of`` links as high edges. Every link is high: a low match
    never writes a ``duplicate_of`` (GROUPING "10"; it is a ``Hint``)."""
    return [
        (row.source_path, row.duplicate_of, "high")
        for row in rows.values() if row.duplicate_of
    ]


HINT_ONLY_PREFIX = "hint only (not grouped): "
BURST_PREFIX = "burst stack (keep all): "
# GROUPING 10 (G7) — appended to an unjoined pair member whose cluster would
# have bridged two groups that content kept apart.
_PAIR_SKIPPED_SUFFIX = "; pair link skipped: its stills are in different groups"


def _mark_hints(rows: dict[str, ManifestRow], hints: list[Hint]) -> None:
    """GROUPING "10" — a low-confidence match never groups, so it is shown as
    text only, on both of its files (``low-confidence match with <partner>``).

    A hint whose two ends already share a group is redundant and is dropped.
    Each remaining end names its first partner by sorted path; the rest are
    counted. An ungrouped row carries the ``HINT_ONLY_PREFIX`` marker, is
    ``low``, and keeps the action it had (undecided, or UNDATED from Pass 3). A
    grouped row only gains a ``hint:`` clause: its action and confidence do not
    move, so a hint cannot change what auto-select does with it."""
    partners: dict[str, list[tuple[str, str]]] = {}
    for a, b, detail in hints:
        if rows[a].group_id is not None and rows[a].group_id == rows[b].group_id:
            continue
        partners.setdefault(a, []).append((b, detail))
        partners.setdefault(b, []).append((a, detail))
    for path in sorted(partners):
        row = rows[path]
        ordered = sorted(partners[path])
        partner, detail = ordered[0]
        more = f", +{len(ordered) - 1} more" if len(ordered) > 1 else ""
        text = f"low-confidence match with {Path(partner).name} ({detail}{more})"
        if row.group_id is None:
            row.match_confidence = "low"
            row.reason = (
                f"{row.reason}; {HINT_ONLY_PREFIX}{text}" if row.reason
                else HINT_ONLY_PREFIX + text
            )
        else:
            row.reason = f"{row.reason}; hint: {text}"


def _burst_frames(group_rows: list[ManifestRow]) -> list[ManifestRow]:
    """The rows of one group that can be burst frames, in (shot time, path)
    order. A frame is pHash-bearing and dated: a Live-Photo video peer has no
    pHash and a UTC date that would open a fake gap."""
    frames = [r for r in group_rows if r.phash and r.shot_date]
    return sorted(
        frames, key=lambda r: (datetime.fromisoformat(r.shot_date), r.source_path)
    )


def _is_burst(times: list[datetime]) -> bool:
    """A same-moment burst: the span is longer than ``BURST_MAX_GAP_S`` (a
    fast burst inside it keeps its status quo) and every consecutive gap is
    within it. One wider gap is a non-burst bridge (undated node, tz-shifted
    copy, Live-Photo pair edge), and the group is left alone."""
    if len(times) < 2:
        return False
    gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:])]
    span = (times[-1] - times[0]).total_seconds()
    return span > BURST_MAX_GAP_S and all(g <= BURST_MAX_GAP_S for g in gaps)


def _relabel_burst(frames: list[ManifestRow], rows: dict[str, ManifestRow]) -> None:
    """Every frame that is not a byte copy becomes undecided ("") at high
    confidence, so no frame is a delete candidate. A byte copy (same sha256 as
    its ``duplicate_of``) keeps its EXACT/high verdict and reason. ``duplicate_of``
    and the group are untouched."""
    span = (
        datetime.fromisoformat(frames[-1].shot_date)
        - datetime.fromisoformat(frames[0].shot_date)
    ).total_seconds()
    n = len({r.source_hash for r in frames})
    for row in frames:
        keeper = rows.get(row.duplicate_of) if row.duplicate_of else None
        if keeper is not None and keeper.source_hash == row.source_hash:
            continue
        old = row.reason
        row.action = ""
        row.match_confidence = "high"
        row.reason = BURST_PREFIX + f"{n} frames over {span:.0f} s"
        if old not in ("", "unique"):
            row.reason += f"; {old}"


def _mark_burst_stacks(rows: dict[str, ManifestRow]) -> None:
    """GROUPING 7 — a same-moment burst inside an already-formed group becomes
    a keep-all stack. Groups are visited in sorted group_id order. Only the
    verdicts of the burst's frames change; no edge is added or merged."""
    by_group: dict[str, list[ManifestRow]] = {}
    for row in rows.values():
        if row.group_id is not None:
            by_group.setdefault(row.group_id, []).append(row)
    for group_id in sorted(by_group):
        frames = _burst_frames(by_group[group_id])
        if _is_burst([datetime.fromisoformat(r.shot_date) for r in frames]):
            _relabel_burst(frames, rows)


def _pair_pass(
    pair_pairs: list[tuple[str, str]],
    find: Callable[[str], str],
    union: Callable[[str, str], None],
    joined: set[str],
    has_still: dict[str, bool],
) -> list[str]:
    """GROUPING "10" (G7) — apply the "pair" edges after the high edges.

    The pairs form clusters of their own (a separate union-find, so the
    clusters never depend on edge order). ``has_still`` maps each component
    root to whether that component holds any still (non-video) file. Per
    cluster, by the roots of its already-``joined`` members:

    * no joined member: nothing joins. A lone Live Photo stays ungrouped.
    * at most one root holds a still: the whole cluster joins, so one capture's
      still and its video-only copies become one group.
    * two or more roots hold a still: content kept those stills apart, so the
      cluster would bridge them. Nothing is unioned, and its unjoined members
      are returned for the caller's reason text.

    After a union the merged component's ``has_still`` is updated, so later
    clusters read current facts. Clusters run in sorted root order, so the
    result is deterministic for a given set of records. Two clusters can share
    one video-only component (byte-identical videos under stills in different
    groups). The first cluster in sorted order takes it, and the second is
    refused with the skip text. No unrelated file ever joins a group.
    """
    parent: dict[str, str] = {}

    def root(x: str) -> str:
        while x in parent:
            x = parent[x]
        return x

    for a, b in pair_pairs:
        ra, rb = root(a), root(b)
        if ra != rb:
            parent[rb] = ra
    clusters: dict[str, list[tuple[str, str]]] = {}
    for a, b in pair_pairs:
        clusters.setdefault(root(a), []).append((a, b))
    skipped: list[str] = []
    for key in sorted(clusters):
        cluster_edges = clusters[key]
        members = {node for edge in cluster_edges for node in edge}
        roots = {find(m) for m in members if m in joined}
        if not roots:
            continue
        if sum(1 for r in roots if has_still.get(r, False)) > 1:
            skipped.extend(sorted(members - joined))
            continue
        touched = {find(m) for m in members}
        merged_still = any(has_still.get(r, False) for r in touched)
        for a, b in cluster_edges:
            union(a, b)
        joined.update(members)
        has_still[find(next(iter(members)))] = merged_still
    return skipped


# GROUPING 10 iteration 3: the hint text for a content edge refused because an
# undated row would have linked two shot moments.
_UNDATED_BRIDGE_DETAIL = "shot time unknown; would link two moments"


def _shot_times(rows: dict[str, ManifestRow]) -> dict[str, datetime]:
    """Each dated row's ``shot_date``, parsed once. A missing or unparseable
    value is undated: the row is absent from the map."""
    times: dict[str, datetime] = {}
    for path, row in rows.items():
        if not row.shot_date:
            continue
        try:
            times[path] = datetime.fromisoformat(row.shot_date)
        except ValueError:
            continue
    return times


def _would_link_two_moments(dated_a: list[datetime], dated_b: list[datetime]) -> bool:
    """True when both components hold a dated row and no dated row of one agrees
    (``_time_vote``) with a dated row of the other. An undated row has no time to
    disagree with, so only the components' dated rows are compared."""
    if not dated_a or not dated_b:
        return False
    return not any(_time_vote(x, y) == "agree" for x in dated_a for y in dated_b)


def _assign_group_ids(
    rows: dict[str, ManifestRow],
    edges: list[Edge] | None = None,
) -> tuple[list[str], list[Hint]]:
    """Assign group_id via union-find over duplicate_of edges + ``edges``.

    Files transitively connected by ``high`` edges (A→B, B→C) all receive the
    same group_id — the lexicographically smallest source_path in the
    component. Isolated files (no edge at all) receive group_id = None.

    ``edges`` carries the non-``duplicate_of`` union edges: the Live Photo
    "pair" edges (content-gated, GROUPING "10") and the pHash near-duplicate
    and complementary edges (#538 / #824). Only ``high`` and ``pair`` edges
    exist: low-confidence evidence is a ``Hint`` and never unions.

    Content edges are unioned in four phases (GROUPING "10", iteration 3):
    identity (a ``duplicate_of`` to an EXACT row, dates ignored); dated-dated
    content; content with an undated end, refused when it would link two shot
    moments (``_would_link_two_moments``) and returned as a hint; then the
    "pair" edges (``_pair_pass``). A cluster joins only through a member that
    is already grouped, and never bridges two components that each hold a
    still.

    Returns the unjoined members of every refused pair cluster, for the
    caller's reason text, and the refused undated hints, for ``_mark_hints``.

    Which lone file pairs with which follows path spelling (the lex-min rule);
    that is deliberate, so a rescore reproduces the same groups.
    """
    parent: dict[str, str] = {}
    shot_times = _shot_times(rows)
    # Component root -> the dated shot times of its rows. A root absent here
    # holds no dated row. Merged on every union.
    dated: dict[str, list[datetime]] = {path: [t] for path, t in shot_times.items()}

    def find(x: str) -> str:
        while x in parent:
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        lo, hi = (ra, rb) if ra < rb else (rb, ra)
        parent[hi] = lo
        dated[lo] = dated.get(lo, []) + dated.pop(hi, [])

    # ``joined``: nodes a high edge touched. A pair cluster joins only through
    # one of these.
    joined: set[str] = set()

    def link(a: str, b: str) -> None:
        union(a, b)
        joined.update((a, b))

    row_edges = _row_edges(rows)
    identity = sorted(e for e in row_edges if rows[e[0]].action == "EXACT")
    row_content = sorted(e for e in row_edges if rows[e[0]].action != "EXACT")
    high_content = sorted(e for e in (edges or []) if e[2] == "high")
    ordered_content = [*row_content, *high_content]

    for a, b, _ in identity:
        link(a, b)
    for a, b, _ in ordered_content:
        if a in shot_times and b in shot_times:
            link(a, b)
    refused: list[Hint] = []
    for a, b, _ in ordered_content:
        if a in shot_times and b in shot_times:
            continue
        if find(a) != find(b) and _would_link_two_moments(
            dated.get(find(a), []), dated.get(find(b), [])
        ):
            refused.append((a, b, _UNDATED_BRIDGE_DETAIL))
        else:
            link(a, b)

    pair_pairs = sorted(
        (min(a, b), max(a, b)) for a, b, confidence in (edges or []) if confidence == "pair"
    )
    # Component root -> does the component hold any still? Built from every row
    # (a still with no group is its own component), after the high unions.
    has_still: dict[str, bool] = {}
    for path in rows:
        r = find(path)
        has_still[r] = has_still.get(r, False) or not is_video(path)
    skipped = _pair_pass(pair_pairs, find, union, joined, has_still)

    for row in rows.values():
        if row.source_path in joined:
            row.group_id = find(row.source_path)
        # else: stays None (isolated file, or a hint, which never groups)
    return skipped, refused


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
        content_identifier=hr.content_identifier,
    )
