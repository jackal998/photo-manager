"""Scenario 36 — destructive Execute through the lock-confirm dialog (#182).

Required source: qa/sandbox/_disposable/s36_source/ — 5 fresh JPEGs
regenerated each run (modeled on s13_execute_action). The scenario
sends them to the operator's Windows recycle bin, so the fixture is
regenerated next run.

The destructive cousin of s34 (which drives Cancel non-destructively).
s34 proves the lock-confirm dialog APPEARS at Execute time; s36 proves
the full chain when the user picks **Unlock & Apply to All** — locked
row gets unlocked, both originally-decided rows AND the previously-
locked row reach ``send2trash``, manifest writes ``executed=1`` for
every row.

Why a separate destructive scenario
-----------------------------------
- s13 destroys files but doesn't exercise locks.
- s34 exercises the dialog appearance but bails on Cancel.
- Without s36 the path "user clicks Execute → lock-confirm fires →
  Unlock & Apply All → send2trash actually fires on the unlocked-
  this-instant row" has only been verified at layer 1 via mocked
  send2trash (test_execute_action_dialog.py::
  TestExecuteRequestedLockConfirm.test_apply_all_unlocked_runs_send2trash_on_both_paths).
  s36 closes the integration gap.

⚠ HEADS-UP: every run sends 5 files to the operator's real Windows
recycle bin (same trade-off as s13, acceptable per the destructive-
scenario guidance in CLAUDE.md).
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from qa.scenarios import _invariants, _uia
# Reuse the fresh-JPEG generator from s13 — same pHash pre-flight
# check, same near-duplicate clustering guarantee.
from qa.scenarios.s13_execute_action import (
    QUALITIES,
    _build_base,
    _max_pairwise_phash,
    _REGEN_MAX_ATTEMPTS,
    _SCANNER_THRESHOLD,
)

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO / "qa" / "sandbox" / "_disposable" / "s36_source"
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"
NUM_FILES = 5

LOCK_REGEX = r"q95"   # matches exactly one fixture file — the lock target
ALL_REGEX = r"s36_"   # matches every fixture file


def _regen_fixture() -> list[Path]:
    """Wipe FIXTURE_DIR and write NUM_FILES fresh near-duplicate JPEGs.

    Mirrors s13_execute_action._regen_fixture but writes to s36's own
    disposable dir so the two destructive scenarios don't race over
    the same files. Uses the same pHash pre-flight check (#148) so the
    five files reliably cluster as a single REVIEW_DUPLICATE group.
    """
    if FIXTURE_DIR.exists():
        for f in FIXTURE_DIR.iterdir():
            if f.is_file():
                f.unlink()
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    last_worst: int | None = None
    base: Image.Image | None = None
    for _attempt in range(_REGEN_MAX_ATTEMPTS):
        candidate = _build_base(np.random.default_rng())
        worst = _max_pairwise_phash(candidate, QUALITIES)
        if worst <= _SCANNER_THRESHOLD:
            base = candidate
            break
        last_worst = worst
    if base is None:
        raise RuntimeError(
            f"Could not generate near-duplicate fixture clustering within "
            f"threshold {_SCANNER_THRESHOLD} after {_REGEN_MAX_ATTEMPTS} "
            f"attempts (last worst pairwise pHash distance was {last_worst})."
        )

    paths: list[Path] = []
    for i, q in enumerate(QUALITIES):
        exif = base.getexif()
        exif[36867] = f"2024:05:01 1{i}:00:00"
        out = FIXTURE_DIR / f"s36_neardup_{i:02d}_q{q}.jpg"
        base.save(str(out), "JPEG", quality=q, exif=exif.tobytes())
        paths.append(out)
    return paths


def _read_lock_and_executed() -> list[tuple[str, int, int]]:
    """Return [(basename, is_locked, executed), …] for s36 fixture rows."""
    if not MANIFEST_PATH.exists():
        raise RuntimeError(f"manifest not found at {MANIFEST_PATH}")
    conn = sqlite3.connect(str(MANIFEST_PATH))
    try:
        rows = conn.execute(
            "SELECT source_path, is_locked, executed FROM migration_manifest "
            "WHERE source_path LIKE ?",
            (f"%{FIXTURE_DIR.name}%",),
        ).fetchall()
    finally:
        conn.close()
    return [(Path(p).name, int(loc or 0), int(ex or 0)) for p, loc, ex in rows]


def main() -> int:
    print("scenario: s36_lock_confirm_destructive_execute")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

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
    _, win = _uia.connect_main()

    # Mark every file delete via the standalone regex menu — no locks
    # in the manifest yet so the lock-confirm dialog must NOT fire.
    print(f"step: bulk_delete regex={ALL_REGEX!r}")
    _uia.mark_all_via_regex_standalone(
        win, field="File Name", regex=ALL_REGEX, action_label="delete"
    )

    # Lock exactly one of the now-marked-delete files (free sentinel —
    # also no confirm). After this step the manifest holds one row with
    # decision='delete' AND is_locked=1 — exactly the state the pre-
    # execute scan in _on_execute_requested is built to detect.
    print(f"step: lock_one regex={LOCK_REGEX!r}")
    _uia.mark_all_via_regex_standalone(
        win, field="File Name", regex=LOCK_REGEX, action_label="lock"
    )

    pre = _read_lock_and_executed()
    locked_before = [n for n, loc, _ex in pre if loc]
    print(f"  locked_before={locked_before}")
    if len(locked_before) != 1:
        print(f"FAIL: expected exactly 1 locked row, got {locked_before}")
        return 1

    print("step: open_execute_action_dialog")
    exec_dlg, _ = _uia.open_execute_action_dialog(win)

    # Click Execute → pre-execute scan finds the locked-delete row →
    # lock-confirm fires → drive Unlock & Apply to All → all-delete
    # confirm Yes → actual send2trash + mark_executed.
    print(
        f"step: execute_via_lock_confirm "
        f"verdict={_uia.LOCK_CONFIRM_APPLY_ALL_UNLOCKED!r}"
    )
    confirm_shape_ok: list[bool] = []

    def _probe_confirm(box):
        confirm_shape_ok.append(_invariants.assert_destructive_confirm_shape(box))

    _uia.execute_and_confirm(
        exec_dlg,
        on_confirm_open=_probe_confirm,
        expect_lock_confirm=_uia.LOCK_CONFIRM_APPLY_ALL_UNLOCKED,
    )
    print("  execute_dialog_closed=True")
    if not confirm_shape_ok or not confirm_shape_ok[0]:
        print("FAIL: destructive-confirm dialog had wrong shape")
        return 1

    print("step: verify_files_removed")
    still_present = [str(p) for p in fixture_paths if p.exists()]
    removed = [str(p) for p in fixture_paths if not p.exists()]
    print(f"  removed_count={len(removed)} still_present_count={len(still_present)}")
    for p in still_present:
        print(f"  STILL_PRESENT: {p}")
    if still_present:
        print(
            "FAIL: some fixture files were not removed by send2trash — "
            "the Unlock & Apply All verdict did not flow through to deletion"
        )
        return 1

    print("step: verify_manifest_executed_and_unlocked")
    post = _read_lock_and_executed()
    not_executed = [n for n, _loc, ex in post if ex != 1]
    still_locked = [n for n, loc, _ex in post if loc]
    print(f"  manifest_rows={len(post)}")
    for name, loc, ex in sorted(post):
        glyph = "🔒" if loc else "  "
        print(f"  row: {glyph} {name}  executed={ex}")
    if not post:
        print("FAIL: no manifest rows matched the fixture path")
        return 1
    if not_executed:
        print(f"FAIL: {len(not_executed)} rows still have executed=0: {not_executed}")
        return 1
    if still_locked:
        print(
            f"FAIL: rows still locked after Unlock & Apply All: {still_locked} "
            "— the verdict's unlock step did not persist"
        )
        return 1

    print("scenario: s36_lock_confirm_destructive_execute DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
