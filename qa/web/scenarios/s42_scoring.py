"""Web scenario s42 — Keep-worthiness scoring pipeline (#187).

Ported from qa/scenarios/s42_scoring.py (Qt UIA / SQLite read).

Scope: COMPOSITE SCORE + PER-DIMENSION SIGNALS.
  #680 serialises gps_present / exif_tag_count / xmp_derived into every
  FileRow (core/app_service/review_view.py:_build_file_row), so this driver
  now asserts the same per-dimension signal propagation the Qt driver reads
  out of SQLite — through GET /api/manifest, without a DB backdoor.  That
  closes the assertion-scope parity gap this scenario shipped with.

Qt intent:
  - Scan qa/sandbox/near-duplicates (5 JPEG re-saves at qualities 95/88/80/72/65
    from one base image).  Score is driven by file_size_bytes (larger file =
    better quality = higher score).  neardup_00_q95.jpg should outscore
    neardup_04_q65.jpg.
  - Scan qa/sandbox/scoring-mixed (4 near-duplicates of one base image that vary
    on dimensions the near-duplicates fixture leaves tied):
      scoring_clean.jpg           — baseline (GPS + clean name + clean path)
      Copy of scoring_clean.jpg   — filename penalty
      scoring_no_gps.jpg          — GPS stripped
      Downloads/scoring_clean.jpg — path penalty
  - Assert the composite score pipeline is wired end-to-end: computation
    (apply_scoring_to_rows), storage (score column), serialisation
    (GET /api/manifest), and ordering (items are score-DESC within each group).

Web slice:
  1. Copy near-duplicates fixtures into tmpdir_nd; scan → nd_manifest.db beside it.
  2. wait_manifest_loaded → GET /api/manifest for nd_manifest.db.
  3. Assert: all items have score in [0.0, 1.0]; at least one non-null score
     (guards silent scoring failure); items are ordered score-DESC (API contract);
     neardup_00_q95 appears before neardup_04_q65 in the items array.
  4. Copy scoring-mixed fixtures into tmpdir_sm (preserving Downloads/ subdir);
     scan → sm_manifest.db beside tmpdir_sm.
  5. wait_manifest_loaded → GET /api/manifest for sm_manifest.db.
  6. Assert: all items have score in [0.0, 1.0]; scoring_clean.jpg outscores all
     three penalised variants (filename / path / GPS-stripped).
  7. Assert per-dimension (#680, mirrors the Qt driver's SQLite reads):
     gps_present true on the three GPS-bearing variants and false on
     scoring_no_gps.jpg; exif_tag_count non-null everywhere (a null means the
     extended EXIF census pass never ran — the #556 starvation symptom);
     xmp_derived false everywhere (the fixture carries no xmpMM:DerivedFrom).

Qt divergences:
  (i)   The Qt driver reads scores directly from SQLite
        (migration_manifest.score); the web port uses GET /api/manifest, which
        is the authoritative serialisation (strictly stronger — tests the full
        data pipeline).
  (ii)  The Qt driver reads the per-dimension signals (gps_present,
        exif_tag_count, xmp_derived) straight out of the SQLite columns; the
        web port reads the SAME three values out of GET /api/manifest, which
        #680 made the serialisation carry.  Assertion scope is now equal; the
        read path is strictly stronger here, because a signal that stops
        crossing the API boundary fails this driver while leaving the DB (and
        therefore the Qt driver) green.
  (iii) The Qt driver scans both fixtures in a single run (PRE_PHOTO_MANAGER
        manifest = qa/run-manifest.sqlite).  The web port does two sequential
        scans into separate tmp manifests so each fixture can be isolated and
        the SINGLETON-DROP rule is respected per-scan (each fixture's files all
        pair within their own group).
  (iv)  The Qt driver verifies the manifest-gated menu items are enabled
        (assert_manifest_actions_consistent).  The web port inherits this from
        s01_happy_path; s42 focuses on the score values.
  (v)   The Qt driver reads keys as scoring-mixed-relative paths (two files share
        basename scoring_clean.jpg).  The web port asserts by file_path suffix to
        correctly distinguish them when two files share a basename.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import run_scan, wait_manifest_loaded

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = _REPO / "qa" / "sandbox" / "near-duplicates"
_SCORING_MIXED_DIR = _REPO / "qa" / "sandbox" / "scoring-mixed"

# All 5 near-duplicate basenames (all pair within the group — no singleton trap).
_NEARDUP_BASENAMES = [
    "neardup_00_q95.jpg",
    "neardup_01_q88.jpg",
    "neardup_02_q80.jpg",
    "neardup_03_q72.jpg",
    "neardup_04_q65.jpg",
]
_NEARDUP_BEST = "neardup_00_q95.jpg"   # largest file → highest composite score
_NEARDUP_WORST = "neardup_04_q65.jpg"  # smallest file → lowest composite score

# scoring-mixed uses file_path SUFFIXES (not basenames) because two files share
# the basename scoring_clean.jpg (one at root, one in Downloads/).
_SM_CLEAN = "scoring_clean.jpg"               # root — clean name + path
_SM_COPY_OF = "Copy of scoring_clean.jpg"     # filename penalty
_SM_NO_GPS = "scoring_no_gps.jpg"             # GPS stripped
_SM_DOWNLOADS = "Downloads/scoring_clean.jpg" # path penalty


# ---------------------------------------------------------------------------
# HTTP helper — mirrors s24 / s56 exactly (stdlib urllib, no requests).
# ---------------------------------------------------------------------------


def _get_manifest(base_url: str, db_path: str) -> dict:
    """Return the full manifest JSON via GET /api/manifest?path=<db_path>."""
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def _sm_suffix(file_path: str) -> str:
    """Return the scoring-mixed-relative suffix for a file_path.

    Takes everything after the 'scoring-mixed' component and joins with
    forward slashes — matching the MIXED_* constants and the Qt read logic
    in qa/scenarios/s42_scoring.py lines 119-124.
    """
    parts = Path(file_path).parts
    try:
        idx = parts.index("scoring-mixed")
    except ValueError:
        # Fallback: use basename only (should not happen for well-formed fixtures).
        return Path(file_path).name
    return "/".join(parts[idx + 1:])


# ---------------------------------------------------------------------------
# Scenario entry point
# ---------------------------------------------------------------------------


def run(*, base_url: str) -> None:
    """Two-scan composite-score validation: near-duplicates then scoring-mixed.

    Raises AssertionError on any regression; the batch runner treats that as
    a failure.  Returns None on success (the batch runner ignores the return
    value).
    """
    tmpdir_nd = tempfile.mkdtemp(prefix="qa_s42_nd_")
    tmpdir_sm = tempfile.mkdtemp(prefix="qa_s42_sm_")
    nd_db = os.path.join(tmpdir_nd, "s42_neardup_manifest.db")
    sm_db = os.path.join(tmpdir_sm, "s42_mixed_manifest.db")

    try:
        # ── Copy near-duplicates fixtures ────────────────────────────────────
        src_nd = os.path.join(tmpdir_nd, "near-duplicates")
        os.makedirs(src_nd, exist_ok=True)
        for basename in _NEARDUP_BASENAMES:
            shutil.copy(str(_NEAR_DUPS_DIR / basename), os.path.join(src_nd, basename))

        # ── Copy scoring-mixed fixtures (preserve Downloads/ subdir) ─────────
        src_sm = os.path.join(tmpdir_sm, "scoring-mixed")
        os.makedirs(src_sm, exist_ok=True)
        os.makedirs(os.path.join(src_sm, "Downloads"), exist_ok=True)
        shutil.copy(
            str(_SCORING_MIXED_DIR / "scoring_clean.jpg"),
            os.path.join(src_sm, "scoring_clean.jpg"),
        )
        shutil.copy(
            str(_SCORING_MIXED_DIR / "Copy of scoring_clean.jpg"),
            os.path.join(src_sm, "Copy of scoring_clean.jpg"),
        )
        shutil.copy(
            str(_SCORING_MIXED_DIR / "scoring_no_gps.jpg"),
            os.path.join(src_sm, "scoring_no_gps.jpg"),
        )
        shutil.copy(
            str(_SCORING_MIXED_DIR / "Downloads" / "scoring_clean.jpg"),
            os.path.join(src_sm, "Downloads", "scoring_clean.jpg"),
        )

        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # ── Scan 1: near-duplicates ──────────────────────────────────────
            run_scan(
                page,
                sources=[src_nd],
                output_path=nd_db,
                scan_timeout=120_000,
            )
            assert os.path.exists(nd_db), f"scan did not write manifest: {nd_db}"

            # ALWAYS wait_manifest_loaded before fetching GET /api/manifest.
            # The SSE 'finished' event fires before loadManifest() resolves;
            # reading between them gets stale/empty data (see _invariants.py
            # wait_manifest_loaded docstring).
            wait_manifest_loaded(page, timeout=30_000)
            nd_manifest = _get_manifest(base_url, nd_db)

            # ── Assert 1: near-duplicates score pipeline ─────────────────────
            nd_groups = nd_manifest.get("groups", [])
            assert len(nd_groups) >= 1, (
                f"near-duplicates scan produced 0 groups; expected at least 1 "
                f"(all 5 neardup files should cluster into 1 duplicate group). "
                f"total_files={nd_manifest.get('total_files')}"
            )

            # Collect all items across all groups.
            nd_items: list[dict] = []
            for group in nd_groups:
                nd_items.extend(group.get("items", []))

            assert len(nd_items) >= 2, (
                f"near-duplicates manifest has {len(nd_items)} items; expected >= 2 "
                f"(fixture has 5 files that should form at least one group)"
            )

            # Guard: at least one non-null score (detects silent scoring failure).
            scored_nd = [it for it in nd_items if it.get("score") is not None]
            assert len(scored_nd) >= 1, (
                f"All {len(nd_items)} near-duplicate items have score=null; "
                f"apply_scoring_to_rows may not be wired into the scan pipeline"
            )

            # Every item with a score must be in [0.0, 1.0].
            for item in nd_items:
                score = item.get("score")
                if score is not None:
                    assert isinstance(score, (int, float)), (
                        f"score type error for {item.get('basename')!r}: "
                        f"expected float, got {type(score).__name__}"
                    )
                    assert 0.0 <= float(score) <= 1.0, (
                        f"score out of range for {item.get('basename')!r}: {score}"
                    )

            # Items must be ordered score-DESC within each group (API contract:
            # MainVM._group_records prepends ("score", False) before serialising).
            for group in nd_groups:
                items = group.get("items", [])
                scores = [
                    float(it["score"]) for it in items if it.get("score") is not None
                ]
                assert scores == sorted(scores, reverse=True), (
                    f"group {group.get('group_number')} items are NOT score-DESC: "
                    f"scores={scores} (expected descending order)"
                )

            # Specific ordering: neardup_00_q95 (largest, best quality) must
            # appear before neardup_04_q65 (smallest, worst quality) in the
            # items array — i.e., have a lower index (higher position = higher score).
            first_group_items = nd_groups[0].get("items", [])
            nd_basenames_in_order = [
                Path(it["file_path"]).name for it in first_group_items
            ]
            if (
                _NEARDUP_BEST in nd_basenames_in_order
                and _NEARDUP_WORST in nd_basenames_in_order
            ):
                idx_best = nd_basenames_in_order.index(_NEARDUP_BEST)
                idx_worst = nd_basenames_in_order.index(_NEARDUP_WORST)
                assert idx_best < idx_worst, (
                    f"score ordering wrong: {_NEARDUP_BEST!r} should appear before "
                    f"{_NEARDUP_WORST!r} in items (score-DESC), but found indices "
                    f"{idx_best} vs {idx_worst}. "
                    f"order={nd_basenames_in_order}"
                )

            # ── Scan 2: scoring-mixed ────────────────────────────────────────
            # Navigate back so run_scan can open the scan dialog cleanly.
            page.goto("/")
            page.wait_for_load_state("networkidle", timeout=10_000)

            # recursive=True is REQUIRED here: the fixture's path-penalty
            # variant lives in a Downloads/ subdirectory, and the ScanDialog's
            # per-row recursive checkbox defaults to UNCHECKED. Without it the
            # subdirectory is never walked, Downloads/scoring_clean.jpg never
            # reaches the manifest, and the path-penalty comparison below
            # silently compares nothing (found while adding the #680
            # per-dimension assertions).
            run_scan(
                page,
                sources=[src_sm],
                output_path=sm_db,
                scan_timeout=120_000,
                recursive=True,
            )
            assert os.path.exists(sm_db), f"scan did not write manifest: {sm_db}"

            wait_manifest_loaded(page, timeout=30_000)
            sm_manifest = _get_manifest(base_url, sm_db)

            # ── Assert 2: scoring-mixed composite ranking ────────────────────
            sm_groups = sm_manifest.get("groups", [])
            assert len(sm_groups) >= 1, (
                f"scoring-mixed scan produced 0 groups; expected at least 1. "
                f"total_files={sm_manifest.get('total_files')}"
            )

            sm_items: list[dict] = []
            for group in sm_groups:
                sm_items.extend(group.get("items", []))

            assert len(sm_items) >= 2, (
                f"scoring-mixed manifest has {len(sm_items)} items; expected "
                f">= 2, i.e. that the scan produced a real duplicate group at "
                f"all. The exact row set is asserted below, per variant"
            )

            # Build {suffix -> score} and {suffix -> full row} maps so we can
            # identify the two files that share the basename scoring_clean.jpg
            # by their path suffix.
            sm_score_by_suffix: dict[str, float | None] = {}
            sm_row_by_suffix: dict[str, dict] = {}
            for item in sm_items:
                suffix = _sm_suffix(item["file_path"])
                raw_score = item.get("score")
                sm_score_by_suffix[suffix] = float(raw_score) if raw_score is not None else None
                sm_row_by_suffix[suffix] = item

            # Guard: at least one non-null score.
            scored_sm = [v for v in sm_score_by_suffix.values() if v is not None]
            assert len(scored_sm) >= 1, (
                f"All scoring-mixed items have score=null; "
                f"apply_scoring_to_rows may not be wired into the scan pipeline"
            )

            # Every non-null score must be in [0.0, 1.0].
            for suffix, score in sm_score_by_suffix.items():
                if score is not None:
                    assert 0.0 <= score <= 1.0, (
                        f"score out of range for {suffix!r}: {score}"
                    )

            # Composite ranking: scoring_clean.jpg should outscore all three
            # penalised variants (filename / path / GPS-stripped penalties).
            # Only assert where both scores are known — if a fixture file was
            # dropped (e.g., singleton), skip that comparison rather than crash.
            clean_score = sm_score_by_suffix.get(_SM_CLEAN)
            if clean_score is not None:
                for penalised_key, penalty_name in [
                    (_SM_COPY_OF, "filename penalty (Copy of …)"),
                    (_SM_DOWNLOADS, "path penalty (Downloads/)"),
                    (_SM_NO_GPS, "GPS-stripped penalty"),
                ]:
                    penalised_score = sm_score_by_suffix.get(penalised_key)
                    if penalised_score is not None:
                        assert clean_score > penalised_score, (
                            f"composite score ranking wrong: {_SM_CLEAN!r} "
                            f"({clean_score:.4f}) should outscore {penalised_key!r} "
                            f"({penalised_score:.4f}) due to {penalty_name}"
                        )

            # ── Assert 3: per-dimension scoring signals (#680) ───────────────
            # Mirrors qa/scenarios/s42_scoring.py::_verify_mixed_pre, which
            # reads the same three values out of the SQLite columns. Reading
            # them through GET /api/manifest is what makes this the API
            # contract rather than a DB backdoor.
            missing = [
                key for key in (_SM_CLEAN, _SM_COPY_OF, _SM_NO_GPS, _SM_DOWNLOADS)
                if key not in sm_row_by_suffix
            ]
            assert not missing, (
                f"scoring-mixed rows missing from the manifest: {missing}; "
                f"present={sorted(sm_row_by_suffix)}. The per-dimension signal "
                f"assertions below need all four variants to be meaningful."
            )

            # The keys must be SERIALISED, not merely falsy. A backend that
            # dropped them would make every `.get(...)` below return None and
            # the value assertions would read as "no GPS anywhere".
            for suffix, row in sm_row_by_suffix.items():
                for key in ("gps_present", "exif_tag_count", "xmp_derived"):
                    assert key in row, (
                        f"FileRow for {suffix!r} has no {key!r} key — "
                        f"core/app_service/review_view.py:_build_file_row must "
                        f"serialise the per-dimension scoring signals (#680). "
                        f"keys={sorted(row)}"
                    )

            # GPS extraction wiring (batch_read_extracts → apply_scoring_to_rows
            # → gps_present column → FileRow). If the exiftool selectors ever
            # lose -GPSLatitude, every file silently reads False and only this
            # assertion notices — the composite score stays plausible.
            for gps_key in (_SM_CLEAN, _SM_COPY_OF, _SM_DOWNLOADS):
                assert sm_row_by_suffix[gps_key]["gps_present"] is True, (
                    f"gps_present is "
                    f"{sm_row_by_suffix[gps_key]['gps_present']!r} for {gps_key!r}, "
                    f"expected True — that fixture carries GPS EXIF"
                )
            assert sm_row_by_suffix[_SM_NO_GPS]["gps_present"] is False, (
                f"gps_present is "
                f"{sm_row_by_suffix[_SM_NO_GPS]['gps_present']!r} for {_SM_NO_GPS!r}, "
                f"expected False — that fixture has its GPS tags stripped"
            )

            # EXIF census wiring: a null count means the extended EXIF pass
            # never ran for that file (#556 exiftool job-nesting starvation
            # produced exactly this, on a non-deterministic subset).
            for suffix, row in sm_row_by_suffix.items():
                assert row["exif_tag_count"] is not None, (
                    f"exif_tag_count is null for {suffix!r} — the extended EXIF "
                    f"census pass did not run for this file (see #556)"
                )

            # xmp_derived must be False (not null) everywhere: the fixture sets
            # no xmpMM:DerivedFrom, and False proves the column populated via
            # the extraction pass rather than never being written.
            for suffix, row in sm_row_by_suffix.items():
                assert row["xmp_derived"] is False, (
                    f"xmp_derived is {row['xmp_derived']!r} for {suffix!r}, "
                    f"expected False — no fixture carries xmpMM:DerivedFrom"
                )

    finally:
        shutil.rmtree(tmpdir_nd, ignore_errors=True)
        shutil.rmtree(tmpdir_sm, ignore_errors=True)
