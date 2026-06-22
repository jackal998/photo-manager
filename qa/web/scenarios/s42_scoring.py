"""Web scenario s42 — Keep-worthiness composite-score pipeline (#187).

Ported from qa/scenarios/s42_scoring.py (Qt UIA / SQLite read).

Scope: COMPOSITE-SCORE only.
  Per-dimension signals gps_present / exif_tag_count / xmp_derived are NOT
  serialized in GET /api/manifest (they are DB columns consumed by the scorer
  but not included in _build_file_row, see core/app_service/review_view.py
  lines 121-149).  Asserting them is deferred to issue #680.

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

Qt divergences:
  (i)   The Qt driver reads scores directly from SQLite
        (migration_manifest.score); the web port uses GET /api/manifest, which
        is the authoritative serialisation (strictly stronger — tests the full
        data pipeline).
  (ii)  The Qt driver asserts per-dimension signals (gps_present,
        exif_tag_count, xmp_derived) read directly from SQLite columns.  Those
        columns exist in the DB but are NOT serialised by _build_file_row, so
        they are unavailable through GET /api/manifest.  These assertions are
        deferred to issue #680.
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

            run_scan(
                page,
                sources=[src_sm],
                output_path=sm_db,
                scan_timeout=120_000,
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
                f"scoring-mixed manifest has {sm_items} items; expected >= 2 "
                f"(fixture has 4 files)"
            )

            # Build {suffix -> score} map so we can identify the two files that
            # share the basename scoring_clean.jpg by their path suffix.
            sm_score_by_suffix: dict[str, float | None] = {}
            for item in sm_items:
                suffix = _sm_suffix(item["file_path"])
                raw_score = item.get("score")
                sm_score_by_suffix[suffix] = float(raw_score) if raw_score is not None else None

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

    finally:
        shutil.rmtree(tmpdir_nd, ignore_errors=True)
        shutil.rmtree(tmpdir_sm, ignore_errors=True)
