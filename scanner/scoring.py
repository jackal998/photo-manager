"""Keep-worthiness scoring for duplicate groups (#187 — PR 3).

Pure quality measurement: ``compute_score(row, group_rows, weights)``
returns a float in ``[0.0, 1.0]`` derived only from the row's own
attributes plus the group context. No filesystem reads, no subprocess
calls, no DB access — given the same inputs the function always returns
the same output. The action layer (PR 6) is responsible for translating
scores into KEEP/DELETE decisions and for respecting user-intent signals
(``is_locked``, ``xmp:Rating``) which this module deliberately ignores.

The scorer evolved from two prior systems (see #187 issue body for
attribution):

* Apple Photos' "highest detail + most metadata" merge framing — keep
  whatever has the most information.
* py-image-dedup's open-source multi-factor priority cascade — same
  signal family but as a strict ordered cascade rather than a weighted
  sum.

This implementation is a two-tier architecture:

  Tier 1 — Categorical gates applied as absolute deductions
    format_penalty:   RAW=0.00  TIFF=0.05  HEIC=0.10  PNG=0.12
                      WebP=0.18  JPEG/Video=0.20  GIF=0.35
    xmp_derived:      −0.30 if xmpMM:DerivedFrom is present

  Tier 2 — Eight weighted continuous signals (configurable weights)
    resolution       w=0.25   within-group normalised pixel count
    exif_complete    w=0.20   census tag count vs. format baseline
    date_provenance  w=0.15   DateTimeOriginal vs. mtime-derived
    gps_present      w=0.08   binary
    filename         w=0.12   penalise copy / (N) / edited / thumb …
    path             w=0.08   penalise Downloads/, WhatsApp/, temp/ …
    live_photo       w=0.07   HEIC with MOV peer > orphan HEIC
    file_size        w=0.05   correlated with resolution within format

Final score = max(0.0, min(1.0, Tier2 − format_penalty − derived_penalty))

Live Photo passenger rule: a ``.mov`` / ``.mp4`` file whose stem matches
a HEIC sibling in the same group receives ``score = None`` — it is the
HEIC's passenger, not a ranking candidate. PR 6's action layer skips
None-score rows when applying decisions.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from scanner.dedup import ManifestRow
    from scanner.media_extract import MediaExtract


# ── Tier 1 — Categorical penalties (absolute deductions, not weight-scaled) ──

FORMAT_PENALTY: dict[str, float] = {
    # RAW formats — no penalty (maximum information preservation).
    "nef": 0.00, "cr2": 0.00, "cr3": 0.00, "arw": 0.00,
    "dng": 0.00, "orf": 0.00, "rw2": 0.00, "raf": 0.00,
    # Lossless near-RAW.
    "tiff": 0.05, "tif": 0.05,
    # Apple lossless-ish (lossy in practice but better than JPEG).
    "heic": 0.10, "heif": 0.10,
    # Lossless container, typically a lossy-original derivative.
    "png": 0.12,
    # Modern lossy.
    "webp": 0.18,
    # Standard lossy + video.
    "jpeg": 0.20, "jpg": 0.20,
    "mp4": 0.20, "mov": 0.20,
    # Legacy / severely limited.
    "gif": 0.35,
}

# Penalty applied when xmpMM:DerivedFrom is present — file is definitively
# a Photoshop/Lightroom-exported derivative, not the original.
DERIVED_PENALTY: float = 0.30

# Fallback for extensions not in FORMAT_PENALTY (treated like a lossy
# unknown — neither rewarded nor maximally penalised).
_DEFAULT_FORMAT_PENALTY: float = 0.20


# ── Tier 2 — Default weights for the continuous composite ───────────────────

DEFAULT_WEIGHTS: dict[str, float] = {
    "resolution":    0.25,
    "file_size":     0.05,   # low — strongly correlated with resolution same-format
    "exif_complete": 0.20,
    "date_prov":     0.15,
    "gps":           0.08,
    "filename":      0.12,
    "path":          0.08,
    "live_photo":    0.07,
}  # must sum to 1.00; validate_weights() enforces this for user-supplied dicts


# EXIF census baselines per file type. The scorer normalises
# ``exif_tag_count`` against the appropriate baseline so images and
# videos are scored on their own scale.
IMAGE_EXIF_CENSUS_BASELINE: int = 16   # see scanner/exif.py::_CENSUS_TAGS (image set)
VIDEO_EXIF_CENSUS_BASELINE: int = 9    # see scanner/exif.py::_CENSUS_TAGS (video set)


# Filename patterns that mark a file as a likely copy / derivative.
# Each hit subtracts 0.30 from a base of 1.0; the floor is 0.0.
#
# Pattern note: ``\b`` (word boundary) cannot be used because Python's regex
# engine treats ``_`` as a word character, so ``\bedited\b`` does NOT match
# ``photo_edited.jpg``. We use ``(?<![A-Za-z])word(?![A-Za-z])`` instead so
# letters are the only boundary — underscores, hyphens, spaces, digits, and
# string start/end all count as separators.
#
# Pattern note (omission): a trailing ``_\d{4}$`` pattern was considered for
# catching "_0001"-style derivative suffixes but rejected — it false-positives
# on camera-native filenames like ``IMG_4567.jpg`` / ``DSC_1234.NEF`` that
# also end in four digits and are originals, not derivatives. The other six
# patterns cover the clear copy/edit/screenshot cases.
_FILENAME_PENALTY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?<![A-Za-z])copy(?![A-Za-z])", re.IGNORECASE),          # "Copy of photo"
    re.compile(r"\(\d+\)"),                                                # "(1)", "(2)"
    re.compile(r"(?<![A-Za-z])edited?(?![A-Za-z])", re.IGNORECASE),       # "photo_edited"
    re.compile(r"(?<![A-Za-z])thumb(?:nail)?(?![A-Za-z])", re.IGNORECASE),# "photo_thumb"
    re.compile(r"(?<![A-Za-z])compressed(?![A-Za-z])", re.IGNORECASE),    # "_compressed"
    re.compile(r"(?<![A-Za-z])screenshot(?![A-Za-z])", re.IGNORECASE),    # "Screenshot 2024-..."
]

# Directory names that mark a file as a likely re-export / sideload.
# Each hit subtracts 0.25 from a base of 1.0; the floor is 0.0. Compared
# case-insensitively against substrings of every path segment.
_BAD_PATH_SEGMENTS: frozenset[str] = frozenset({
    "downloads",
    "whatsapp",
    "screenshots", "screenshot",
    "recycle.bin", "$recycle.bin",
    "trash", ".trash",
    "temp", "tmp",
    "telegram",
    "instagram",
    "messenger",
})

# Video suffixes — used by Live Photo peer detection AND by the EXIF-
# completeness baseline (video rows are scored against the smaller video
# tag census, not the image one). #461: ".avi" included so AVI rows (now
# that they reach scoring) get the video treatment, not the image baseline.
_VIDEO_SUFFIXES: frozenset[str] = frozenset({"mov", "mp4", "avi"})
# HEIC suffixes used by the Live Photo peer detection.
_HEIC_SUFFIXES: frozenset[str] = frozenset({"heic", "heif"})


# ── Helpers ─────────────────────────────────────────────────────────────────


def _suffix(row: "ManifestRow") -> str:
    """Lowercase file extension without the leading dot."""
    return Path(row.source_path).suffix.lstrip(".").lower()


def _stem_lower(row: "ManifestRow") -> str:
    """Lowercase filename stem (case-insensitive match for cross-OS fixtures)."""
    return Path(row.source_path).stem.lower()


def _has_paired_peer_with_suffixes(
    row: "ManifestRow",
    group_rows: Iterable["ManifestRow"],
    suffix_set: frozenset[str],
) -> bool:
    """True iff any other row in ``group_rows`` shares this row's stem
    (case-insensitive) and has a suffix in ``suffix_set``."""
    stem = _stem_lower(row)
    for peer in group_rows:
        if peer.source_path == row.source_path:
            continue
        if _stem_lower(peer) == stem and _suffix(peer) in suffix_set:
            return True
    return False


def validate_weights(weights: dict[str, float]) -> None:
    """Raise ValueError if ``weights`` is missing keys or doesn't sum to 1.0.

    Called by the scan pipeline / rescore path before invoking the scorer.
    A bad config produces a clear, early error instead of silently scoring
    on the wrong axes. Tolerance is ±0.001 to absorb float-summation noise.
    """
    missing = set(DEFAULT_WEIGHTS) - set(weights)
    if missing:
        raise ValueError(
            f"scoring weights missing keys: {sorted(missing)} "
            f"(expected {sorted(DEFAULT_WEIGHTS)})"
        )
    total = sum(weights[k] for k in DEFAULT_WEIGHTS)
    if abs(total - 1.0) > 0.001:
        raise ValueError(
            f"scoring weights must sum to 1.0 (got {total:.4f})"
        )


# ── Tier 2 dimension functions (each returns float in [0.0, 1.0]) ───────────


def _score_resolution(
    row: "ManifestRow", group_rows: list["ManifestRow"]
) -> float:
    """Within-group min-max normalised pixel count.

    Edge cases:
    * Row missing pixel_width/height (video, hash failure): 0.0.
    * No other row in the group has dims: 0.0 — nothing to normalise against.
    * All rows in the group tied: 1.0 — no discriminating power.
    """
    px = (row.pixel_width or 0) * (row.pixel_height or 0)
    if px == 0:
        return 0.0
    group_px = [
        (r.pixel_width or 0) * (r.pixel_height or 0)
        for r in group_rows
        if r.pixel_width and r.pixel_height
    ]
    if not group_px:
        return 0.0
    lo, hi = min(group_px), max(group_px)
    if hi == lo:
        return 1.0
    return (px - lo) / (hi - lo)


def _score_file_size(
    row: "ManifestRow", group_rows: list["ManifestRow"]
) -> float:
    """Within-group min-max normalised file size.

    Low weight in DEFAULT_WEIGHTS (0.05) because resolution and file size
    are strongly correlated within the same format. The signal is kept
    for cross-format groups (RAW vs JPEG of the same scene) where file
    size adds independent information.
    """
    size = row.file_size_bytes or 0
    if size == 0:
        return 0.0
    group_sizes = [r.file_size_bytes for r in group_rows if r.file_size_bytes]
    if not group_sizes:
        return 0.0
    lo, hi = min(group_sizes), max(group_sizes)
    if hi == lo:
        return 1.0
    return (size - lo) / (hi - lo)


def _score_exif_completeness(row: "ManifestRow") -> float:
    """Census tag count normalised against the file-type baseline.

    Image baseline: 16 census tags (EXIF + XMP user metadata).
    Video baseline: 9 census tags (QuickTime + GPSCoordinates).
    Old manifests with no extended exiftool pass have count=None → 0.0.
    """
    count = row.exif_tag_count
    if count is None:
        return 0.0
    baseline = (
        VIDEO_EXIF_CENSUS_BASELINE
        if _suffix(row) in _VIDEO_SUFFIXES
        else IMAGE_EXIF_CENSUS_BASELINE
    )
    return min(count / baseline, 1.0)


def _score_date_provenance(row: "ManifestRow") -> float:
    """1.0 if shot_date is real EXIF; 0.3 if shot_date looks mtime-derived;
    0.0 if no shot_date at all.

    The 2-second tolerance catches the common pattern where a file copy
    inherits the filesystem timestamp and the scanner stores that as
    shot_date because no real EXIF DateTimeOriginal exists. Without
    storing the source tag separately in the DB, this comparison is the
    best heuristic available.

    Tz-mismatch pragmatic fallback (#467): ``shot_date`` is camera-local
    with tz info stripped (scanner/exif.py), while ``mtime`` is
    scanner-local (scanner/dedup.py uses ``fromtimestamp``). When
    camera and scanner are in different zones, a real EXIF date can be
    off from mtime by a whole-hour multiple. The 2s gate doesn't trip
    on the resulting large diff (the score happens to be 1.0 already),
    but the explicit hour-multiple check makes the invariant
    load-bearing — if the gate ever widened, the tz-mismatch case
    would otherwise be wrongly demoted. Strict tz-aware handling via
    OffsetTimeOriginal is deferred until scanner/exif.py is unlocked
    by #460's PR merge.
    """
    if row.shot_date is None:
        return 0.0
    if row.mtime is not None:
        try:
            shot = datetime.fromisoformat(row.shot_date)
            mt = datetime.fromisoformat(row.mtime)
            diff = abs((shot - mt).total_seconds())
            # Same-tz mtime-derived: very small diff. Check FIRST because
            # the tz-mismatch test below would also match diff < 2.0
            # (remainder == diff in that range), which would wrongly
            # promote a suspicious row.
            if diff < 2.0:
                return 0.3   # suspicious: shot_date == mtime, likely derived
            # Cross-tz tz-strip artefact: diff is close to a whole-hour
            # multiple — likely a real EXIF date that just looks far
            # from mtime because of the camera↔scanner tz delta.
            remainder = diff % 3600.0
            if remainder < 2.0 or remainder > 3598.0:
                return 1.0
        except (ValueError, TypeError):
            # Malformed date string — fall through to the default below.
            pass
    return 1.0


def _score_gps(row: "ManifestRow") -> float:
    """Binary GPS presence — 1.0 if any GPS tag was extracted, else 0.0.

    Precision (DOP) and altitude are intentionally ignored: GPS presence
    is already a strong rarity signal (< 30% of consumer photos), and
    altitude is present in < 10% of GPS photos — binary is adequate.
    """
    return 1.0 if row.gps_present else 0.0


def _score_filename(row: "ManifestRow") -> float:
    """Penalty score from filename patterns that mark a file as a copy /
    derivative. Each pattern hit subtracts 0.30 from a 1.0 base; floor 0.0.
    """
    stem = Path(row.source_path).stem
    hits = sum(bool(p.search(stem)) for p in _FILENAME_PENALTY_PATTERNS)
    return max(0.0, 1.0 - 0.30 * hits)


def _score_path(row: "ManifestRow") -> float:
    """Penalty score from directory path. Each path segment matching a
    'bad' substring (Downloads, WhatsApp, Screenshots, temp, …)
    subtracts 0.25 from a 1.0 base; floor 0.0.

    Only directory segments are inspected — the basename is scored
    by ``_score_filename`` and excluding it here avoids double-counting
    (#466). Each segment contributes at most one −0.25, even if it
    matches multiple bad-keyword keys (e.g. a folder named
    "screenshots" hits both ``screenshots`` and ``screenshot``).
    """
    parts_lower = [p.lower() for p in Path(row.source_path).parent.parts]
    hits = sum(
        1 for seg in parts_lower
        if any(bad in seg for bad in _BAD_PATH_SEGMENTS)
    )
    return max(0.0, 1.0 - 0.25 * hits)


def _score_live_photo(
    row: "ManifestRow", group_rows: list["ManifestRow"]
) -> float:
    """Live Photo completeness signal for HEIC files.

    * HEIC with a paired MOV/MP4 in the same group: 1.0.
    * HEIC without a paired video: 0.5 (orphan).
    * Anything else (JPEG, RAW, PNG, standalone video): 1.0 (dimension N/A).

    The MOV side of a pair is handled separately by ``compute_score``,
    which returns ``None`` for the MOV — it doesn't compete with its HEIC.
    """
    suffix = _suffix(row)
    if suffix not in _HEIC_SUFFIXES:
        return 1.0
    has_video_peer = _has_paired_peer_with_suffixes(
        row, group_rows, _VIDEO_SUFFIXES
    )
    return 1.0 if has_video_peer else 0.5


# ── Public API ──────────────────────────────────────────────────────────────


def compute_score(
    row: "ManifestRow",
    group_rows: list["ManifestRow"],
    weights: dict[str, float] = DEFAULT_WEIGHTS,
) -> Optional[float]:
    """Composite keep-worthiness score in ``[0.0, 1.0]``, or ``None``.

    Returns ``None`` when ``row`` is a Live Photo MOV/MP4 passenger
    (its stem matches a HEIC sibling in the same group). The MOV
    inherits its HEIC's KEEP/DELETE decision in the action layer and
    is not a standalone ranking candidate.

    Pure function: no I/O, no globals. Same (row, group_rows, weights)
    always produces the same result.
    """
    # Live Photo passenger rule.
    if _suffix(row) in _VIDEO_SUFFIXES and _has_paired_peer_with_suffixes(
        row, group_rows, _HEIC_SUFFIXES
    ):
        return None

    # Tier 1 — categorical penalties (absolute, not weight-scaled).
    fmt_penalty = FORMAT_PENALTY.get(_suffix(row), _DEFAULT_FORMAT_PENALTY)
    derived_penalty = DERIVED_PENALTY if row.xmp_derived else 0.0

    # Tier 2 — weighted continuous composite.
    tier2 = (
        weights["resolution"]    * _score_resolution(row, group_rows)
        + weights["file_size"]     * _score_file_size(row, group_rows)
        + weights["exif_complete"] * _score_exif_completeness(row)
        + weights["date_prov"]     * _score_date_provenance(row)
        + weights["gps"]           * _score_gps(row)
        + weights["filename"]      * _score_filename(row)
        + weights["path"]          * _score_path(row)
        + weights["live_photo"]    * _score_live_photo(row, group_rows)
    )

    return max(0.0, min(1.0, tier2 - fmt_penalty - derived_penalty))


def score_group(
    group_rows: list["ManifestRow"],
    weights: dict[str, float] = DEFAULT_WEIGHTS,
) -> dict[str, Optional[float]]:
    """Score every row in a duplicate group.

    Returns a mapping from ``source_path`` to score (or ``None`` for Live
    Photo MOV passengers). The action layer (PR 6) walks this dict,
    skips None entries and locked rows, then applies KEEP/DELETE to the
    survivors.

    Pure function — sample fixtures and weights, get deterministic scores.
    """
    return {
        r.source_path: compute_score(r, group_rows, weights)
        for r in group_rows
    }


def apply_scoring_to_rows(
    rows: "list[ManifestRow]",
    extracts: "dict[Path, MediaExtract]",
    weights: dict[str, float] = DEFAULT_WEIGHTS,
) -> None:
    """Wire MediaExtract scoring signals into ManifestRows + assign scores.

    Two-phase mutation of ``rows`` in place:

    Phase A — Backfill raw signals from the exiftool extract dict:
        ``exif_tag_count``, ``gps_present``, ``xmp_derived`` move from
        the MediaExtract (PR 2 output) onto ManifestRow (PR 1 column).
        The MediaExtract sentinel ``None`` (not checked) is preserved as
        the column default — only definite True/False / int values
        overwrite. Rows whose path is missing from ``extracts`` (exiftool
        skipped or failed for that file) keep their default ManifestRow
        values; the scorer treats those as 'no signal' (0.0).

    Phase B — Compute and assign composite scores per group:
        Rows are grouped by ``group_id`` (None / isolated rows stay
        unscored). Within each group, ``score_group`` produces the dict;
        ``ManifestRow.score`` is set per row. Live Photo MOV passengers
        receive ``None`` (passed through from score_group); the action
        layer (PR 6) will skip them when applying decisions.

    Pure-with-mutation: same inputs always produce the same row state.
    No I/O. Tested via ``test_apply_scoring_to_rows`` with synthetic
    ManifestRow + MediaExtract fixtures.
    """
    from collections import defaultdict

    # Phase A: backfill raw scoring signals from exiftool extracts.
    for row in rows:
        extract = extracts.get(Path(row.source_path))
        if extract is None:
            continue
        if extract.exif_tag_count is not None:
            row.exif_tag_count = extract.exif_tag_count
        # MediaExtract uses Optional[bool] (None=not checked). ManifestRow's
        # column is NOT NULL DEFAULT 0 — collapse None to the existing
        # default rather than overwriting.
        if extract.gps_present is not None:
            row.gps_present = bool(extract.gps_present)
        if extract.xmp_derived is not None:
            row.xmp_derived = bool(extract.xmp_derived)

    # Phase B: compute scores within each duplicate group. Isolated rows
    # (group_id is None) intentionally stay unscored — they have no peers
    # to compete with.
    groups: dict[str, list] = defaultdict(list)
    for row in rows:
        if row.group_id:
            groups[row.group_id].append(row)
    for group_rows in groups.values():
        scores = score_group(group_rows, weights)
        for row in group_rows:
            row.score = scores[row.source_path]
