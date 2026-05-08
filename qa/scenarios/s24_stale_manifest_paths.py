"""Scenario 24 — Open manifest whose source files no longer exist (#123).

Pins the real-world common case of opening a valid sqlite manifest that
references files that have since been moved/deleted (drive unmounted,
NAS offline, cleanup pass). Distinct from s16's *corrupt-sqlite* error
path: this is *valid sqlite, dead paths*.

Required source: ``qa/sandbox/_disposable/s24_source`` — driver
generates the contents at startup and **deletes them** before the
re-load step. The fixture is regenerated next run; this is the
"forwards-only" pattern from s13.

## Flow

  1. Generate fixture: 3 fresh JPEGs in ``_disposable/s24_source/``.
  2. Open scan dialog → run scan → Close & Load. The manifest at
     ``qa/run-manifest.sqlite`` now references those source paths and
     they all currently exist.
  3. Delete the entire ``_disposable/s24_source/`` directory. The
     manifest now points at dead paths.
  4. File → Open Manifest → re-open ``qa/run-manifest.sqlite``.
     ``ManifestRepository.load`` is read-only against sqlite and
     should not crash regardless of source-path existence.
  5. Verify: no error dialog, status bar shows the success pattern,
     tree has the expected row count, basenames are still readable
     from sqlite. Capture observed UX state on stale rows for the
     log so a follow-up issue can pin specific findings.

## What's verified vs documented

  * **Verified (FAIL on regression)**: load succeeds; status bar
    matches the same shape s16 verifies for happy-path open;
    manifest-gated menu items remain consistently enabled; **all
    expected basenames are present in the raw TreeItem set after the
    stale re-load** — i.e. sqlite is the source of truth and the load
    path does NOT silently drop rows whose source files are missing.
  * **Observed (printed, not asserted)**: post-scan and post-stale-open
    raw TreeItem counts. We use ``descendants(control_type="TreeItem")``
    rather than ``read_result_rows`` here because the latter has a
    ``y_min=600`` filter that silently drops tree rows on small
    windows (the post-scan fixture is only 3 files, so all rows fall
    above the filter cutoff and the helper returns 0). The raw probe
    sees them all.

## Out of scope

  * Execute Action → delete on a stale row. ``delete_to_recycle()``
    raises mid-session per #68. That issue owns the delete-side fix;
    re-deletion testing is a follow-up scenario after #68 lands.
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from qa.scenarios import _invariants, _uia

REPO = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO / "qa" / "sandbox" / "_disposable" / "s24_source"
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"
NUM_FILES = 3


def _setup_fixture() -> list[Path]:
    """Wipe FIXTURE_DIR and write NUM_FILES fresh JPEGs.

    Files are deliberately distinct (different solid colours + EXIF
    dates) so the scanner classifies them as MOVE (isolated single-
    item groups), not REVIEW_DUPLICATE — keeps the manifest shape
    simple. We don't need pHash clustering for this scenario; just
    rows in the manifest that we can later orphan.
    """
    if FIXTURE_DIR.exists():
        shutil.rmtree(FIXTURE_DIR)
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    paths: list[Path] = []
    for i in range(NUM_FILES):
        # Solid distinct colour per file → unique pHash → distinct rows.
        arr = np.full((100, 100, 3), [(i * 80) % 256, 100, 200], dtype=np.uint8)
        img = Image.fromarray(arr)
        exif = img.getexif()
        exif[36867] = f"2024:08:01 1{i}:00:00"  # DateTimeOriginal
        out = FIXTURE_DIR / f"s24_photo_{i:02d}.jpg"
        img.save(str(out), "JPEG", quality=85, exif=exif.tobytes())
        paths.append(out)
    return paths


def _delete_fixture() -> None:
    """Orphan the manifest by removing every source file."""
    if FIXTURE_DIR.exists():
        shutil.rmtree(FIXTURE_DIR)


def main() -> int:
    print("scenario: s24_stale_manifest_paths")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

    # ── Setup: regenerate fixture so the scan has files to walk ──────────
    print("step: setup_fixture")
    fixture_paths = _setup_fixture()
    expected_basenames = sorted(p.name for p in fixture_paths)
    print(f"  fixture_dir={FIXTURE_DIR}")
    print(f"  fixture_count={len(fixture_paths)}")
    print(f"  expected_basenames={expected_basenames}")

    # ── Scan + load — produces qa/run-manifest.sqlite ────────────────────
    print("step: open_scan_dialog")
    dlg, _ = _uia.open_scan_dialog(win)
    print(f"  configured_sources={_uia.read_configured_sources(dlg)!r}")

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=30)
    print(f"  scan_elapsed_s={elapsed:.2f}")
    for line in _uia.extract_summary(log):
        if line:
            print(f"  log: {line}")

    print("step: close_and_load")
    _uia.close_and_load_manifest(dlg)
    if not MANIFEST_PATH.exists():
        print(f"FAIL: scan did not produce manifest at {MANIFEST_PATH}")
        return 1

    # ── Baseline observation: tree state with all source files present ──
    # Captured for the qa-batch log so a future change to the post-load
    # tree-population behaviour shows up as a diff. Not asserted —
    # whether MOVE singletons appear in the tree alongside REVIEW pairs
    # is a UX choice this scenario doesn't pin.
    #
    # Reports BOTH ``read_result_rows`` (which y_min-filters to skip the
    # column-header strip) and raw ``descendants("TreeItem")`` so a
    # discrepancy between the two — e.g. rows present but above the
    # y_min cutoff — is visible from the log alone.
    print("step: observe_tree_post_scan")
    _, win = _uia.connect_main()
    rows_post_scan = _uia.read_result_rows(win)
    raw_items_post_scan = win.descendants(control_type="TreeItem")
    raw_basenames_post_scan: set[str] = set()
    for it in raw_items_post_scan:
        try:
            txt = (it.window_text() or "").strip()
            if txt and (txt.lower().endswith(".jpg") or txt.lower().endswith(".jpeg")):
                raw_basenames_post_scan.add(txt)
        except Exception:
            continue
    seen_basenames_post_scan: set[str] = set()
    for r in rows_post_scan:
        for c in r.cells:
            cl = c.lower()
            if cl.endswith(".jpg") or cl.endswith(".jpeg"):
                seen_basenames_post_scan.add(c)
    print(f"  rows_post_scan={len(rows_post_scan)}  "
          f"raw_treeitems={len(raw_items_post_scan)}")
    print(f"  basenames_post_scan(filtered)={sorted(seen_basenames_post_scan)}")
    print(f"  basenames_post_scan(raw)={sorted(raw_basenames_post_scan)}")

    # ── Orphan the manifest by deleting every source file ────────────────
    print("step: delete_fixture_to_orphan_manifest")
    _delete_fixture()
    if FIXTURE_DIR.exists():
        print(f"FAIL: fixture dir still exists after rmtree: {FIXTURE_DIR}")
        return 1
    print(f"  fixture_dir_exists_after_delete={FIXTURE_DIR.exists()}")

    # ── Re-open the manifest. ManifestRepository.load is read-only against
    # sqlite — load should not depend on source-path existence at all.
    # If it does, that's the bug. ──────────────────────────────────────────
    print("step: open_stale_manifest")
    _, win = _uia.connect_main()
    _uia.menu_path(win, _uia.MENU_FILE, _uia.FILE_OPEN_MANIFEST)
    try:
        status_at_load = _uia.open_manifest_via_native_dialog(
            pid, str(MANIFEST_PATH.resolve())
        )
    except RuntimeError as exc:
        print(f"FAIL: stale-manifest open raised RuntimeError: {exc}")
        return 1
    print(f"  status_at_load={status_at_load!r}")

    print("step: assert_status_shape_matches_happy_path")
    # The status bar shape is the same as a healthy-manifest open: the
    # load step doesn't know about file existence yet, so the success
    # phrasing is identical. Mirror s16's tight match.
    if not re.search(
        r"Opened manifest: \d+ pairs? to review \(\d+ files?\)",
        status_at_load,
    ):
        # Fall back to a looser match — earlier builds used a slightly
        # different phrasing on the no-pairs case.
        if not re.search(r"Opened manifest:", status_at_load):
            print(f"FAIL: status shape mismatch — got {status_at_load!r}")
            return 1
        print(f"  status_shape=loose-match (no 'pair to review' suffix)")

    # ── Observe the tree after the stale re-load. Names live in sqlite;
    # whether the view-model surfaces them when the underlying files
    # are gone is a UX choice. Capture for log-diff visibility, do not
    # assert. ──────────────────────────────────────────────────────────
    print("step: observe_tree_post_stale_open")
    _, win = _uia.connect_main()
    rows_post_stale = _uia.read_result_rows(win)
    raw_items_post_stale = win.descendants(control_type="TreeItem")
    raw_basenames_post_stale: set[str] = set()
    for it in raw_items_post_stale:
        try:
            txt = (it.window_text() or "").strip()
            if txt and (txt.lower().endswith(".jpg") or txt.lower().endswith(".jpeg")):
                raw_basenames_post_stale.add(txt)
        except Exception:
            continue
    seen_basenames_post_stale: set[str] = set()
    for r in rows_post_stale:
        for c in r.cells:
            cl = c.lower()
            if cl.endswith(".jpg") or cl.endswith(".jpeg"):
                seen_basenames_post_stale.add(c)
    print(f"  rows_post_stale={len(rows_post_stale)}  "
          f"raw_treeitems={len(raw_items_post_stale)}")
    print(f"  basenames_post_stale(filtered)={sorted(seen_basenames_post_stale)}")
    print(f"  basenames_post_stale(raw)={sorted(raw_basenames_post_stale)}")
    print(
        f"  observation: raw treeitems changed from "
        f"{len(raw_items_post_scan)} (post-scan) to "
        f"{len(raw_items_post_stale)} (post-stale-open); "
        f"sqlite row count reported in status bar is unchanged"
    )

    failures: list[str] = []

    # Pin: every basename written into sqlite by the scan MUST still be
    # in the raw TreeItem set after the stale re-load. sqlite is the
    # authoritative source of row identity; file existence is a
    # rendering concern, not a load concern. A future regression that
    # added "skip rows whose source_path doesn't exist" to the load
    # path would lose this assertion.
    missing_after_stale = set(expected_basenames) - raw_basenames_post_stale
    if missing_after_stale:
        failures.append(
            f"basenames missing from tree after stale-open: "
            f"{sorted(missing_after_stale)}; sqlite has them but the "
            f"load path silently dropped them — the load layer should "
            f"be source-existence-agnostic (rendering can decorate or "
            f"hide; the model should not filter)"
        )

    # Manifest-gated menu items must be enabled — manifest IS loaded,
    # even if its rows reference dead paths.
    print("step: invariant_actions_after_stale_load")
    inv_actions = _invariants.assert_manifest_actions_consistent(
        win, expected_enabled=True
    )
    if not inv_actions:
        failures.append(
            "manifest-gated menu items NOT all enabled after stale-manifest "
            "load — manifest is loaded so they should be live"
        )

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("scenario: s24_stale_manifest_paths DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
