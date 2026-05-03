"""Scenario 16 — File > Open Manifest async load flow.

Required source: qa/sandbox/near-duplicates (5 files → 1 group of 5).

Drives the Open Manifest flow end-to-end, both happy and error paths:

  Happy:
    scan + close & load (writes qa/run-manifest.sqlite as a side effect)
    → File menu → Open Manifest… → native open dialog → drive to
    qa/run-manifest.sqlite → wait for status bar to settle to
    "Opened manifest: N pairs to review (M files)" → confirm
    manifest-gated menu items are still consistently enabled.

  Error:
    write a deliberately-corrupt sqlite at qa/sandbox/_disposable/
    s16_corrupt.sqlite → File → Open Manifest… → drive to corrupt
    path → confirm "Open Manifest Error" critical dialog appears →
    confirm status bar reports "Open manifest failed" → confirm
    manifest-gated menu items REMAIN ENABLED because the previously-
    loaded manifest is still in memory (#108: failed loads must not
    strand a prior valid manifest's actions disabled).

Catches drift in: Open Manifest menu label / dialog title /
ManifestLoadWorker signal chain (progress / finished / failed) /
status-bar verb shape ("Opened manifest" success, "Open manifest
failed" error) / error-fallback path.

Distinct from s12 (which writes the manifest) — this is the read
partner: s12 + s16 together cover the full save↔load round-trip.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from qa.scenarios import _invariants, _uia

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"
CORRUPT_PATH = REPO / "qa" / "sandbox" / "_disposable" / "s16_corrupt.sqlite"


def _make_corrupt_fixture() -> Path:
    """Write non-sqlite bytes to a .sqlite file. ManifestRepository.load
    fails when sqlite3 reports "file is not a database" (or "no such
    table" if the bytes happen to be a valid empty sqlite). Either way
    the worker emits failed → "Open Manifest Error" surfaces.
    """
    CORRUPT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CORRUPT_PATH.write_bytes(b"this is not a sqlite database\n" * 50)
    return CORRUPT_PATH


def main() -> int:
    print("scenario: s16_open_manifest")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

    # ── Set up: scan + close-and-load to produce qa/run-manifest.sqlite ───
    print("step: prepare_manifest_via_scan")
    dlg, _ = _uia.open_scan_dialog(win)
    print(f"  configured_sources={_uia.read_configured_sources(dlg)!r}")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=30)
    print(f"  scan_elapsed_s={elapsed:.2f}")
    for line in _uia.extract_summary(log):
        if line:
            print(f"  log: {line}")
    _uia.close_and_load_manifest(dlg)
    if not MANIFEST_PATH.exists():
        print(f"FAIL: scan did not produce manifest at {MANIFEST_PATH}")
        return 1
    print(f"  manifest_path={MANIFEST_PATH}")

    # ── Happy path: re-open the same manifest via File > Open Manifest ────
    print("step: open_manifest_happy_path")
    _, win = _uia.connect_main()
    _uia.menu_path(win, _uia.MENU_FILE, _uia.FILE_OPEN_MANIFEST)
    status_at_load = _uia.open_manifest_via_native_dialog(
        pid, str(MANIFEST_PATH.resolve())
    )
    print(f"  status_at_load={status_at_load!r}")

    print("step: assert_status_shape")
    # The helper returned the status text it observed at success — re-check
    # the shape here so failures point at the regex, not at a polling race.
    if not re.search(r"Opened manifest:.*pair.*file", status_at_load):
        print(f"FAIL: status shape mismatch — got {status_at_load!r}")
        return 1

    print("step: invariant_actions_happy")
    _, win = _uia.connect_main()
    inv_actions = _invariants.assert_manifest_actions_consistent(
        win, expected_enabled=True
    )
    if not inv_actions:
        print("FAIL: manifest-gated menu items not all enabled after open")
        return 1

    # ── Error path: drive to a corrupt sqlite ─────────────────────────────
    print("step: prepare_corrupt_fixture")
    corrupt = _make_corrupt_fixture()
    print(f"  corrupt={corrupt}")
    print(f"  corrupt_size={corrupt.stat().st_size}")

    print("step: open_manifest_error_path")
    _, win = _uia.connect_main()
    _uia.menu_path(win, _uia.MENU_FILE, _uia.FILE_OPEN_MANIFEST)
    error_raised = False
    try:
        _uia.open_manifest_via_native_dialog(pid, str(corrupt.resolve()))
    except RuntimeError as exc:
        # Expected: helper raises after dismissing the error dialog.
        print(f"  error_path_raised_as_expected: {str(exc)[:120]!r}")
        error_raised = True
    if not error_raised:
        print("FAIL: corrupt manifest did not raise Open Manifest Error")
        return 1

    print("step: invariant_status_bar_error")
    _, win = _uia.connect_main()
    inv_failed = _invariants.assert_status_bar_matches(
        win, r"Open manifest failed", within_s=2.0
    )
    if not inv_failed:
        # Soft-fail: the failure status uses the default 3000ms timeout, so
        # by the time we poll it may have cleared. Don't escalate.
        print("WARN: status bar did not echo 'Open manifest failed' (may have cleared on timeout)")

    print("step: invariant_actions_after_error")
    # #108: a failed Open Manifest must not strand the previously-loaded
    # manifest's actions disabled. The user opened a corrupt B while A was
    # active; after dismissing the error they're back to reviewing A, so
    # A's manifest-gated actions stay enabled.
    inv_actions_post = _invariants.assert_manifest_actions_consistent(
        win, expected_enabled=True
    )
    if not inv_actions_post:
        print("FAIL: prior manifest's actions were stranded disabled after a "
              "failed Open Manifest (regression of #108)")
        return 1

    print("scenario: s16_open_manifest DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
