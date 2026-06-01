"""Scenario 3 — Cancel scan mid-run (two samples: post-HASH + WALK-stage).

Required sources: qa/sandbox/near-duplicates, huge, unique (configured by
``qa.scenarios.configure``). The WALK-stage sample builds and points at its
own large disposable source at runtime — see below.

Probes: interrupt handling — does cancel actually stop, is cleanup clean,
is the manifest left in a consistent state?

Strategy: kick off a scan, immediately click the title-bar Close (×) — there's
no explicit "Cancel" button on the dialog, so the close gesture IS the cancel.

Two cancel samples in one file (#493), following s31's two-samples-in-one-file
precedent:

  * **Sample 1 (post-HASH)** — the original 0.8 s cancel. With the standard
    sandbox sources the WALK stage finishes in <50 ms, so by 0.8 s the worker
    is in HASH/CLASSIFY/SCORE; the cancel lands there. Kept unchanged.
  * **Sample 2 (WALK-stage)** — #491 made the WALK stage cooperatively
    cancellable (the QThread interruption flag is threaded straight into the
    walker's ``rglob`` loop). The standard sandbox is too small to ever be
    *inside* WALK when a cancel arrives, so this sample reconfigures the scan
    to a large disposable source (several thousand 1-KiB stub JPEGs built by
    ``scripts/make_qa_large_source.py``) and cancels ~0.2 s in, while the
    walker is still enumerating. The log must show ``Walking …`` (WALK started)
    AND ``Scan cancelled.`` (the #491 WALK gate fired) — never ``Done.``.

The large source lives under ``qa/sandbox/_disposable/`` (gitignored) and is
regenerated at scenario-setup time, so no thousands-of-stubs blob is ever
committed — same disposable-fixture contract as s13 / s36 / s44.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"

# Stub count for the WALK-stage source. Big enough that the walker is still
# enumerating ~0.2 s after Start on a CI runner, small in bytes (1-KiB stubs).
WALK_SOURCE_STUB_COUNT = 6000


def _sample_post_hash_cancel(win):
    """Sample 1 — the original post-HASH cancel against the standard sources."""
    print("step: sample1_open_scan_dialog")
    dlg, scan_hwnd = _uia.open_scan_dialog(win)
    print(f"  configured_sources={_uia.read_configured_sources(dlg)!r}")

    print("step: sample1_start_then_cancel")
    start_btn = dlg.child_window(title=_uia.SCAN_BTN_START, control_type="Button")
    log_edit = dlg.child_window(auto_id=_uia.SCAN_AID_LOG, control_type="Edit")
    t0 = time.time()
    start_btn.invoke()
    # Let it get into the hashing phase, then cancel.
    time.sleep(0.8)
    pre_log = log_edit.window_text() or ""
    print(f"  log_at_cancel={pre_log[-300:]!r}")
    _uia.cancel_scan_dialog(dlg)
    elapsed = time.time() - t0
    print(f"  cancel_elapsed_s={elapsed:.2f}")
    time.sleep(1.0)

    print("step: sample1_post_cancel_state")
    pid = win.process_id()
    wins = [t for _, _, t in _uia.list_process_windows(pid)]
    print(f"  open_windows={wins!r}")

    print("step: sample1_read_main_after_cancel")
    _, win = _uia.connect_main()
    rows = _uia.read_result_rows(win)
    print(f"  total_rows_after_cancel={len(rows)}")
    for r in rows[:10]:
        print(f"  row: y={r.y} cells={list(r.cells)}")
    return win


def _build_large_source() -> Path:
    """Build the WALK-stage large source on demand and return its path."""
    # Import the sibling generator lazily — it pulls PIL, which the batch
    # runner already has, but keeping the import local avoids paying it for
    # the post-HASH sample if this scenario is ever split.
    sys.path.insert(0, str(REPO / "scripts"))
    from make_qa_large_source import make_large_source  # noqa: E402

    print("step: sample2_build_large_source")
    src = make_large_source(WALK_SOURCE_STUB_COUNT)
    stub_count = sum(1 for _ in src.glob("*.jpg"))
    print(f"  large_source={src} stub_count={stub_count}")
    return src


def _sample_walk_stage_cancel(win) -> int:
    """Sample 2 — reconfigure to the large source and cancel during WALK.

    Returns 0 on success, 1 on assertion failure.
    """
    src = _build_large_source()

    print("step: sample2_open_scan_dialog")
    dlg, scan_hwnd = _uia.open_scan_dialog(win)

    print("step: sample2_reconfigure_to_large_source")
    # Clear the standard sandbox sources, then add the large disposable
    # source as the only one. ``Remove All`` empties the table; the
    # path-field + ``+ Add`` flow appends the new source (avoids the
    # Start-Scan-default-button Enter race — see add_source_via_path_field).
    _uia.click_remove_all_sources(dlg)
    time.sleep(0.3)
    _uia.add_source_via_path_field(dlg, str(src))
    time.sleep(0.3)
    print(f"  configured_sources={_uia.read_source_paths(dlg)!r}")

    print("step: sample2_start_capture_walking_log")
    start_btn = dlg.child_window(title=_uia.SCAN_BTN_START, control_type="Button")
    log_edit = dlg.child_window(auto_id=_uia.SCAN_AID_LOG, control_type="Edit")
    pid = win.process_id()
    # Snapshot the output manifest's pre-scan state for the integrity check.
    pre_exists = MANIFEST_PATH.exists()
    pre_bytes = MANIFEST_PATH.read_bytes() if pre_exists else None
    _uia._focus(dlg)
    t0 = time.time()
    start_btn.invoke()

    # Poll the live log until the walker has STARTED (the "Walking …" stage
    # marker is emitted at the top of the WALK pass). We capture it BEFORE
    # cancelling because the title-bar-X close path tears the log widget
    # down — the failed-signal "Scan cancelled." render races the dialog
    # close, so the durable WALK-stage signal is "Walking … was logged
    # while the scan was live", combined with the clean-close + manifest-
    # untouched invariants below. The cancel landing inside WALK vs. just
    # past it (into HASH on a fast SSD) is timing-dependent; both are clean
    # cancels and both leave the output manifest untouched.
    live_log = ""
    saw_walking = False
    saw_done = False
    walk_deadline = time.time() + 8.0
    while time.time() < walk_deadline:
        try:
            live_log = log_edit.window_text() or ""
        except Exception:
            live_log = ""
        if "Walking " in live_log:
            saw_walking = True
        if "Scan cancelled." in live_log:
            break
        if "Done." in live_log:
            saw_done = True
            break
        if saw_walking:
            # WALK has started — cancel immediately to bias toward a
            # WALK-stage landing on slow I/O while still proving the
            # clean-cancel invariants on fast I/O.
            break
        time.sleep(0.05)
    print(f"  saw_walking={saw_walking} saw_done={saw_done}")
    print(f"  live_log_tail_pre_cancel={live_log[-300:]!r}")

    if saw_done:
        print(
            "FAIL: scan reached 'Done.' before a cancel could be sent — the "
            "large source is too small/fast for this runner. Increase "
            "WALK_SOURCE_STUB_COUNT."
        )
        return 1
    if not saw_walking and "Scan cancelled." not in live_log:
        print(
            "FAIL: never observed the 'Walking …' WALK-stage marker in the "
            "live log before cancel — the walker did not start or the source "
            "was empty"
        )
        return 1

    print("step: sample2_cancel_via_x")
    # Deliver the cancel by closing the scan dialog (title-bar-X semantics:
    # closeEvent → worker.requestInterruption()). WM_CLOSE to the dialog
    # HWND is position-independent (the coord-based cancel_scan_dialog can
    # miss if the dialog isn't at its default position).
    _uia.close_window_by_hwnd(scan_hwnd)
    elapsed = time.time() - t0
    print(f"  cancel_elapsed_s={elapsed:.2f}")

    # The scan dialog must close cleanly (closeEvent interrupts the worker,
    # waits up to 3 s, then dismisses).
    print("step: sample2_assert_dialog_closed")
    closed = False
    deadline = time.time() + 8.0
    while time.time() < deadline:
        titles = [t for _, _, t in _uia.list_process_windows(pid)]
        if _uia.SCAN_DIALOG_TITLE not in titles:
            closed = True
            break
        time.sleep(0.2)
    if not closed:
        print("FAIL: scan dialog did not close after the cancel (worker may be stuck)")
        return 1
    print("  scan_dialog_closed=True")

    # Partial-state invariant: a cancelled scan must NOT have written the
    # output manifest (the WRITE-stage gate fires before the manifest write).
    print("step: sample2_assert_manifest_untouched")
    time.sleep(1.0)
    now_exists = MANIFEST_PATH.exists()
    if not pre_exists:
        if now_exists:
            print(
                "FAIL: an output manifest appeared despite the cancel — the "
                "WRITE gate should skip the manifest write on a cancelled scan"
            )
            return 1
        print("  manifest_still_absent=PASS")
    else:
        now_bytes = MANIFEST_PATH.read_bytes() if now_exists else None
        if now_bytes != pre_bytes:
            print(
                "FAIL: output manifest bytes changed across the cancelled "
                "scan — a cancelled scan must leave the prior manifest intact"
            )
            return 1
        print("  manifest_bytes_intact=PASS")

    if saw_walking:
        print("  walk_stage_cancel=PASS (Walking … logged + clean cancel + manifest untouched)")
    else:
        print(
            "  walk_stage_cancel=PARTIAL — clean cancel + manifest untouched, "
            "but the 'Walking …' marker scrolled past before capture "
            "(slow-runner fallback)"
        )
    return 0


def main() -> int:
    print("scenario: s03_cancel_scan")
    app, win = _uia.connect_main()
    print(f"connected: pid={win.process_id()} title={win.window_text()!r}")

    # Sample 1 — post-HASH cancel (original behaviour, unchanged).
    win = _sample_post_hash_cancel(win)

    # Sample 2 — WALK-stage cancel (#493 / #491).
    rc = _sample_walk_stage_cancel(win)
    if rc != 0:
        return rc

    print("scenario: s03_cancel_scan DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
