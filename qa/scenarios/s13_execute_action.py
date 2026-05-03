"""Scenario 13 — Execute Action (destructive: sends files to recycle bin).

Required source: qa/sandbox/_disposable/s13_source/ (regenerated each run by
the driver — 5 fresh JPEGs that get sent to the user's recycle bin when
Execute fires).

Drives the destructive Execute Action flow end-to-end:
  regen disposable fixture (5 JPEGs) → scan → close & load →
  open Execute Action dialog → Set by Field/Regex (field=File Name,
  regex=.+, action=delete) → Apply → Close inner dialog → click Execute →
  confirm "All Files Will Be Deleted" → Yes →
  verify (a) original files no longer exist, (b) manifest rows for those
  paths have executed=1.

⚠ HEADS-UP: every run sends 5 files to the operator's real Windows recycle
bin. The fixture is regenerated next run, so the bin grows by 5 each run
until manually emptied. Acceptable per #80's destructive-scenario guidance.

Catches drift in: Execute Action menu label / Execute dialog title / amber
warning trigger / confirmation prompt copy / send2trash integration /
ManifestRepository.mark_executed() write path.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO / "qa" / "sandbox" / "_disposable" / "s13_source"
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"
NUM_FILES = 5


def _regen_fixture() -> list[Path]:
    """Wipe FIXTURE_DIR and write NUM_FILES fresh near-duplicate JPEGs.

    Mirrors scripts/make_qa_sandbox.make_near_duplicates: one base gradient
    image, saved at descending JPEG qualities with distinct EXIF dates.
    The scanner groups them as REVIEW_DUPLICATE/EXACT (one near-duplicate
    group of NUM_FILES) so they show up in the Execute Action dialog's
    review tree. Random gradients (no fixed seed) so each run produces
    a fresh content set the operator's recycle bin won't already hold.
    """
    if FIXTURE_DIR.exists():
        for f in FIXTURE_DIR.iterdir():
            if f.is_file():
                f.unlink()
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng()
    base_color = rng.integers(0, 256, size=(3,))
    fx = float(rng.uniform(0.5, 4.0))
    fy = float(rng.uniform(0.5, 4.0))
    h, w = 480, 640
    yy, xx = np.meshgrid(np.arange(h), np.arange(w), indexing="ij")
    arr = np.zeros((h, w, 3), dtype=np.float32)
    for c in range(3):
        arr[..., c] = (
            base_color[c]
            + 60 * np.sin(2 * np.pi * fx * xx / w + c)
            + 60 * np.cos(2 * np.pi * fy * yy / h + c * 0.7)
        )
    base = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))

    qualities = [95, 88, 80, 72, 65]
    paths: list[Path] = []
    for i, q in enumerate(qualities):
        exif = base.getexif()
        exif[36867] = f"2024:05:01 1{i}:00:00"  # DateTimeOriginal
        out = FIXTURE_DIR / f"s13_neardup_{i:02d}_q{q}.jpg"
        base.save(str(out), "JPEG", quality=q, exif=exif.tobytes())
        paths.append(out)
    return paths


def main() -> int:
    print("scenario: s13_execute_action")
    app, win = _uia.connect_main()
    print(f"connected: pid={win.process_id()} title={win.window_text()!r}")

    print("step: regen_fixture")
    fixture_paths = _regen_fixture()
    print(f"  fixture_dir={FIXTURE_DIR}")
    print(f"  fixture_count={len(fixture_paths)}")

    print("step: open_scan_dialog")
    dlg, _ = _uia.open_scan_dialog(win)
    print(f"  configured_sources={_uia.read_configured_sources(dlg)!r}")

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=30)
    print(f"  scan_elapsed_s={elapsed:.2f}")
    for line in _uia.extract_summary(log):
        if line:
            print(f"  log: {line}")

    print("step: close_dialog")
    _uia.close_and_load_manifest(dlg)

    print("step: open_execute_dialog")
    _, win = _uia.connect_main()
    exec_dlg, _ = _uia.open_execute_action_dialog(win)

    print("step: mark_all_delete_via_regex")
    _uia.mark_all_via_regex(
        exec_dlg, field="File Name", regex=".+", action_label="delete"
    )

    print("step: execute_and_confirm")
    _uia.execute_and_confirm(exec_dlg)
    print("  execute_dialog_closed=True")

    print("step: verify_files_removed")
    still_present = [str(p) for p in fixture_paths if p.exists()]
    removed = [str(p) for p in fixture_paths if not p.exists()]
    print(f"  removed_count={len(removed)}")
    print(f"  still_present_count={len(still_present)}")
    for p in still_present:
        print(f"  STILL_PRESENT: {p}")
    if still_present:
        print("FAIL: some fixture files were not removed by send2trash")
        return 1

    print("step: verify_manifest_executed")
    if not MANIFEST_PATH.exists():
        print(f"FAIL: manifest not found at {MANIFEST_PATH}")
        return 1
    conn = sqlite3.connect(str(MANIFEST_PATH))
    try:
        rows = conn.execute(
            "SELECT source_path, executed FROM migration_manifest "
            "WHERE source_path LIKE ?",
            (f"%{FIXTURE_DIR.name}%",),
        ).fetchall()
        print(f"  matched_rows={len(rows)}")
        not_executed = [r[0] for r in rows if r[1] != 1]
        for r in rows:
            print(f"  row: source_path={r[0]} executed={r[1]}")
        if not rows:
            print("FAIL: no manifest rows matched the fixture path")
            return 1
        if not_executed:
            print(f"FAIL: {len(not_executed)} rows still have executed=0")
            return 1
    finally:
        conn.close()

    print("scenario: s13_execute_action DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
