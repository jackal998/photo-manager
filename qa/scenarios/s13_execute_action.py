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

import io
import sqlite3
import sys
from pathlib import Path

import imagehash
import numpy as np
from PIL import Image

from qa.scenarios import _invariants, _uia

REPO = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO / "qa" / "sandbox" / "_disposable" / "s13_source"
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"
NUM_FILES = 5
QUALITIES = [95, 88, 80, 72, 65]

# Must match scanner/dedup.py:96 default. The scanner groups files whose
# pairwise pHash Hamming distance is in [1, _SCANNER_THRESHOLD] as
# REVIEW_DUPLICATE; outside that band a file is independently classified
# as MOVE. The s13 scenario assumes all NUM_FILES land in a single
# REVIEW_DUPLICATE group so mark_all_via_regex reaches every row in the
# Execute Action dialog tree. Issue #148 documents the flake mode.
_SCANNER_THRESHOLD = 10
_REGEN_MAX_ATTEMPTS = 5


def _build_base(rng: np.random.Generator) -> Image.Image:
    """Build the per-run gradient base image. Random per call so visual
    content varies across runs (the operator's recycle bin doesn't fill
    with visually-identical entries). Caller is responsible for verifying
    the resulting JPEG-compressed set actually pHash-clusters; see
    ``_regen_fixture``."""
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
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def _max_pairwise_phash(base: Image.Image, qualities: list[int]) -> int:
    """Return the max pairwise pHash Hamming distance across the JPEG-
    compressed versions of ``base`` at each quality. Used as a pre-flight
    check before writing the fixture to disk."""
    saved: list[Image.Image] = []
    for q in qualities:
        buf = io.BytesIO()
        base.save(buf, "JPEG", quality=q)
        buf.seek(0)
        saved.append(Image.open(buf).copy())
    hashes = [imagehash.phash(im) for im in saved]
    return max(
        hashes[i] - hashes[j]
        for i in range(len(hashes))
        for j in range(i + 1, len(hashes))
    )


def _regen_fixture() -> list[Path]:
    """Wipe FIXTURE_DIR and write NUM_FILES fresh near-duplicate JPEGs.

    Mirrors scripts/make_qa_sandbox.make_near_duplicates: one base gradient
    image, saved at descending JPEG qualities with distinct EXIF dates.
    The scanner groups them as REVIEW_DUPLICATE (one near-duplicate group
    of NUM_FILES) so they show up in the Execute Action dialog's review
    tree. Random gradients (no fixed seed) so each run produces a fresh
    content set the operator's recycle bin won't already hold.

    Verify-and-retry (#148): occasional rolls of the random gradient
    parameters produce 5 JPEGs whose pairwise pHash distances exceed the
    scanner's default threshold, so the scanner classifies them as a
    MOVE/REVIEW_DUPLICATE mix instead of a single REVIEW_DUPLICATE group.
    The scenario then breaks because mark_all_via_regex only reaches the
    REVIEW_DUPLICATE rows. Empirically ~5% of seedless rolls fail this
    check (see tmp/probe_s13_clustering.py study). To eliminate the
    flake, generate up to ``_REGEN_MAX_ATTEMPTS`` candidates and pre-flight
    check each via ``_max_pairwise_phash`` before writing to disk. Five
    retries reduce per-run flake probability to (0.05)^5 ≈ 3e-7 — i.e.
    structurally never.
    """
    if FIXTURE_DIR.exists():
        for f in FIXTURE_DIR.iterdir():
            if f.is_file():
                f.unlink()
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    last_worst: int | None = None
    base: Image.Image | None = None
    for attempt in range(_REGEN_MAX_ATTEMPTS):
        candidate = _build_base(np.random.default_rng())
        worst = _max_pairwise_phash(candidate, QUALITIES)
        if worst <= _SCANNER_THRESHOLD:
            base = candidate
            break
        last_worst = worst
    if base is None:
        raise RuntimeError(
            f"Could not generate near-duplicate fixture that clusters "
            f"within the scanner's default threshold {_SCANNER_THRESHOLD} "
            f"after {_REGEN_MAX_ATTEMPTS} attempts; last worst pairwise "
            f"pHash distance was {last_worst}. The gradient parameter "
            "ranges in _build_base may need tightening."
        )

    paths: list[Path] = []
    for i, q in enumerate(QUALITIES):
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
    confirm_shape_ok: list[bool] = []

    def _probe_confirm(box):
        confirm_shape_ok.append(_invariants.assert_destructive_confirm_shape(box))

    _uia.execute_and_confirm(exec_dlg, on_confirm_open=_probe_confirm)
    print("  execute_dialog_closed=True")
    if not confirm_shape_ok or not confirm_shape_ok[0]:
        print("FAIL: destructive-confirm dialog had wrong shape")
        return 1

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
