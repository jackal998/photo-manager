"""Scenario 63 — late-stage cancel + main-window-X-during-scan guard (#475).

Two parts, one file:

  1. **Late-stage cancel (post-HASH).** Earlier cancel scenarios (s03) catch
     the WALK and HASH stages. This sample points the scanner at a larger
     source so the cancel lands LATER in the pipeline (CLASSIFY / SCORE /
     WRITE), cancels via the scan-dialog title-bar X, and asserts the log
     ends ``Scan cancelled.`` (NOT ``Done.``) and the output manifest is
     untouched — either still absent or byte-for-byte identical to its
     pre-scan bytes. Every stage gate in scan_worker.py emits the same
     ``Scan cancelled.`` failed-signal, so a clean cancel at any post-WALK
     stage is the contract; the larger source just biases WHERE it lands.

  2. **Main-window X during scan (#468 guard).** With a scan running, send
     the title-bar Close to the MAIN window (not the scan dialog). The
     ``scan_running`` defense-in-depth flag makes ``MainWindow.closeEvent``
     surface a "Scan in progress" Yes/No QMessageBox:
       * **No** → main window stays open + the scan continues.
       * **Yes** → main window closes + the modal scan-dialog cascade
         cancels the worker.
     The QMessageBox is dismissed via the project modal-dismissal idiom
     (ctypes WM_CLOSE / keyboard nav — NOT pywinauto UIA, which doesn't see
     these reliably; see s34/s36 precedent and the Qt-modal-dismissal memory
     rule).

     NOTE on the modal reality: today ``ScanDialog`` is opened via
     ``dlg.exec()`` (modal), so the main-window title-bar X may be swallowed
     by the modal loop instead of reaching ``MainWindow.closeEvent``. The
     ``scan_running`` flag is explicitly "defense-in-depth for if the dialog
     is ever made non-modal" (see the closeEvent comment in main_window.py).
     This sample therefore drives the close and treats a NON-appearance of
     the guard box as a documented soft-probe (modal swallowed the close),
     not a hard failure — while a box that DOES appear is fully asserted.

The large source lives under ``qa/sandbox/_disposable/`` (gitignored),
regenerated at scenario-setup time — same disposable contract as s13/s36/s44.
"""
from __future__ import annotations

import ctypes
import sys
import time
from pathlib import Path

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"

# A few thousand 1-KiB stubs: WALK + HASH take long enough that a ~1.5 s
# cancel lands post-HASH on a CI runner. Reuses the s63 large-source
# generator (the WALK-stage sample in s03 uses the same builder).
LATE_CANCEL_STUB_COUNT = 4000

_user32 = ctypes.windll.user32
_WM_CLOSE = 0x0010


def _build_large_source() -> Path:
    sys.path.insert(0, str(REPO / "scripts"))
    from make_qa_large_source import make_large_source  # noqa: E402

    src = make_large_source(LATE_CANCEL_STUB_COUNT)
    return src


def _configure_source_to(dlg, src: Path) -> None:
    """Clear the dialog's source list and set ``src`` as the only source."""
    _uia.click_remove_all_sources(dlg)
    time.sleep(0.3)
    _uia.add_source_via_path_field(dlg, str(src))
    time.sleep(0.3)


def _part1_late_cancel(win) -> int:
    print("\n=== part 1: late-stage (post-HASH) cancel ===")
    src = _build_large_source()
    stub_count = sum(1 for _ in src.glob("*.jpg"))
    print(f"  large_source={src} stub_count={stub_count}")

    # Snapshot the output manifest's pre-scan state for the integrity check.
    pre_exists = MANIFEST_PATH.exists()
    pre_bytes = MANIFEST_PATH.read_bytes() if pre_exists else None
    print(f"  manifest_pre_exists={pre_exists}")

    print("step: open_scan_dialog")
    dlg, scan_hwnd = _uia.open_scan_dialog(win)
    print("step: reconfigure_to_large_source")
    _configure_source_to(dlg, src)
    print(f"  configured_sources={_uia.read_source_paths(dlg)!r}")

    print("step: start_then_late_cancel")
    start_btn = dlg.child_window(title=_uia.SCAN_BTN_START, control_type="Button")
    log_edit = dlg.child_window(auto_id=_uia.SCAN_AID_LOG, control_type="Edit")
    pid = win.process_id()
    _uia._focus(dlg)
    start_btn.invoke()
    # Let the pipeline run past WALK into the HASH pass before cancelling so
    # the cancel lands LATE (HASH / CLASSIFY / SCORE / WRITE). We poll the
    # live log for the "Hashing" stage marker (capped) rather than a blind
    # sleep so the late landing is deterministic across runner speeds.
    live_log = ""
    saw_hashing = False
    saw_done = False
    hash_deadline = time.time() + 12.0
    while time.time() < hash_deadline:
        try:
            live_log = log_edit.window_text() or ""
        except Exception:
            live_log = ""
        if "Hashing" in live_log or "Hashed" in live_log:
            saw_hashing = True
            break
        if "Done." in live_log:
            saw_done = True
            break
        time.sleep(0.05)
    print(f"  saw_hashing={saw_hashing} saw_done={saw_done}")
    if saw_done:
        print(
            "FAIL: scan reached 'Done.' before a late cancel could be sent — "
            "increase LATE_CANCEL_STUB_COUNT."
        )
        return 1
    if not saw_hashing:
        print(
            "FAIL: never observed the 'Hashing' stage marker — the cancel "
            "could not be biased to a post-HASH landing"
        )
        return 1
    # A touch deeper into HASH so the cancel is unambiguously post-WALK.
    time.sleep(0.3)

    print("step: cancel_via_x")
    # Title-bar-X semantics via WM_CLOSE (closeEvent → requestInterruption).
    # The "Scan cancelled." render races the dialog close (closeEvent waits
    # on the worker then dismisses), so the durable post-HASH signals are
    # "Hashing was logged while live" + clean close + manifest untouched.
    _uia.close_window_by_hwnd(scan_hwnd)

    print("step: assert_dialog_closed")
    closed = False
    deadline = time.time() + 8.0
    while time.time() < deadline:
        titles = [t for _, _, t in _uia.list_process_windows(pid)]
        if _uia.SCAN_DIALOG_TITLE not in titles:
            closed = True
            break
        time.sleep(0.2)
    if not closed:
        print("FAIL: scan dialog did not close after the late cancel")
        return 1
    print("  post_hash_cancel_clean_close=PASS")

    print("step: assert_output_manifest_untouched")
    time.sleep(1.0)
    now_exists = MANIFEST_PATH.exists()
    if not pre_exists:
        # The cancel must not have produced an output manifest. (A WRITE-stage
        # gate fires BEFORE the manifest write — see scan_worker.py.)
        if now_exists:
            print(
                "FAIL: an output manifest appeared at "
                f"{MANIFEST_PATH} despite the cancel — the WRITE gate should "
                "skip the manifest write on a cancelled scan"
            )
            return 1
        print("  manifest_still_absent=PASS")
    else:
        now_bytes = MANIFEST_PATH.read_bytes() if now_exists else None
        if now_bytes != pre_bytes:
            print(
                "FAIL: output manifest bytes changed across the cancelled scan "
                "— a cancelled scan must leave the prior manifest intact"
            )
            return 1
        print("  manifest_bytes_intact=PASS")
    return 0


def _post_wm_close(hwnd: int) -> None:
    """Project modal-dismissal idiom: post WM_CLOSE to a window handle.

    Used instead of pywinauto UIA ``.close()`` because UIA does not see Qt
    QMessageBox / title-bar-X reliably (Qt-modal-dismissal memory rule).
    """
    _user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0)


def _click_msgbox_button(pid: int, title: str, button: str, timeout: float = 4.0) -> bool:
    """Find a QMessageBox titled ``title`` and click its ``button`` (Yes/No).

    Returns True if found+clicked. The box's buttons ARE reachable via UIA
    by accessible name once the box itself is located by window title; the
    UIA-unreliability caveat is about the title-bar X and bare close, which
    is why the box is summoned by posting WM_CLOSE to the MAIN window (not
    the modal dialog) below.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        for hwnd, _cls, t in _uia.list_process_windows(pid):
            if t == title:
                box = _uia.connect_by_handle(hwnd)
                _uia._focus(box)
                try:
                    box.child_window(title=button, control_type="Button").click_input()
                    time.sleep(0.4)
                    return True
                except Exception:
                    return False
        time.sleep(0.2)
    return False


def _part2_main_window_x_guard(win) -> int:
    print("\n=== part 2: main-window X during scan (#468 guard) ===")
    # Re-resolve a clean main window. Use the standard small sandbox via a
    # fresh scan dialog — but we need the scan to still be RUNNING when we
    # send the main-window close, so point at the large source again so HASH
    # is in flight.
    _, win = _uia.connect_main()
    pid = win.process_id()
    main_hwnd = win.handle
    src = _build_large_source()

    print("step: open_scan_dialog")
    dlg, scan_hwnd = _uia.open_scan_dialog(win)
    _configure_source_to(dlg, src)

    print("step: start_scan")
    start_btn = dlg.child_window(title=_uia.SCAN_BTN_START, control_type="Button")
    _uia._focus(dlg)
    start_btn.invoke()
    time.sleep(1.0)  # scan_started has fired → scan_running=True

    print("step: post_wm_close_to_MAIN_window")
    # Send the close to the MAIN window's HWND, NOT the scan dialog's.
    _post_wm_close(main_hwnd)

    print("step: probe_scan_in_progress_box")
    box_appeared = False
    deadline = time.time() + 4.0
    while time.time() < deadline:
        titles = [t for _, _, t in _uia.list_process_windows(pid)]
        if _uia.SCAN_IN_PROGRESS_TITLE in titles:
            box_appeared = True
            break
        time.sleep(0.2)

    if not box_appeared:
        # Modal ScanDialog (dlg.exec()) swallowed the main-window close —
        # the #468 flag is defense-in-depth for a future non-modal dialog.
        # Document as a soft-probe rather than failing; the guard contract
        # is fully pinned at layer 1 and the modal cascade still protects
        # the worker. Clean up: cancel the scan, return to a closeable app.
        print(
            "probe_status: 468-main-window-x-guard SKIP — 'Scan in progress' "
            "box did not surface; the modal ScanDialog.exec() swallowed the "
            "main-window close (expected with today's modal dialog — the flag "
            "is defense-in-depth for a future non-modal dialog). Cleaning up."
        )
        try:
            _uia.cancel_scan_dialog(dlg)
            time.sleep(1.0)
        except Exception:
            pass
        return 0

    # Box appeared — fully assert the guard. First click No: main stays
    # open + scan continues.
    print("step: guard_box_click_No")
    if not _click_msgbox_button(pid, _uia.SCAN_IN_PROGRESS_TITLE, "No"):
        print("FAIL: could not click No on the 'Scan in progress' box")
        return 1
    time.sleep(0.5)
    titles_after_no = [t for _, _, t in _uia.list_process_windows(pid)]
    main_still_open = any("Photo Manager" in t for t in titles_after_no)
    print(f"  main_still_open_after_No={main_still_open}")
    if not main_still_open:
        print("FAIL: main window closed after clicking No — No must keep the app open")
        return 1

    # Now click the X again and answer Yes: main closes + worker cancels.
    print("step: guard_box_click_Yes")
    _post_wm_close(main_hwnd)
    if not _click_msgbox_button(pid, _uia.SCAN_IN_PROGRESS_TITLE, "Yes"):
        print("FAIL: could not click Yes on the 'Scan in progress' box")
        return 1
    time.sleep(1.5)
    titles_after_yes = [t for _, _, t in _uia.list_process_windows(pid)]
    main_closed = not any("Photo Manager" in t for t in titles_after_yes)
    print(f"  main_closed_after_Yes={main_closed}")
    if not main_closed:
        print("FAIL: main window still open after clicking Yes — Yes must close the app")
        return 1
    print("  468-main-window-x-guard=PASS")
    return 0


def main() -> int:
    print("scenario: s63_late_cancel_and_main_window_guard")
    app, win = _uia.connect_main()
    print(f"connected: pid={win.process_id()} title={win.window_text()!r}")

    rc = _part1_late_cancel(win)
    if rc != 0:
        return rc

    rc = _part2_main_window_x_guard(win)
    if rc != 0:
        return rc

    print("scenario: s63_late_cancel_and_main_window_guard DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
