"""Scenario 27 — Re-scan while manifest has pending decisions: confirmation prompt (#142).

Required source: qa/sandbox/near-duplicates (5 files, basenames neardup_NN_qXX.jpg).

Drives the re-scan-with-pending-decisions confirmation flow end-to-end:
  scan → close & load → set decision on row 0 →
  open scan dialog → click Start Scan →
  (a) verify "Discard pending decisions?" dialog appears, click No →
      verify scan did NOT run (log empty, original manifest intact);
  (b) close scan dialog, set another decision, re-open scan dialog →
      click Start Scan → click Yes on the prompt →
      verify scan ran (Done. in log) and manifest re-built.

Catches drift in: ScanDialog.should_proceed callback wiring;
MainWindow._confirm_no_pending_decisions detection logic; the prompt's
Yes/No button shape; scan-launch-cancellation behaviour.

Note: this scenario does NOT verify the (separate) case where no
decisions are pending — that's the default ScanDialog behaviour and is
covered by every other scan-running scenario in the batch (s01, s10,
s17, etc.). If they pass, we know the gate doesn't fire spuriously.
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

from pywinauto.application import Application

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"

ROW_FIRST = "neardup_00_q95.jpg"
ROW_SECOND = "neardup_01_q88.jpg"

PROMPT_TITLE = "Discard pending decisions?"


def _read_decision_count() -> int:
    """Return the number of rows in the manifest with non-empty user_decision."""
    if not MANIFEST_PATH.exists():
        return 0
    conn = sqlite3.connect(str(MANIFEST_PATH))
    try:
        (n,) = conn.execute(
            "SELECT COUNT(*) FROM migration_manifest "
            "WHERE user_decision IS NOT NULL AND user_decision != ''"
        ).fetchone()
    finally:
        conn.close()
    return n


def _wait_for_prompt(pid: int, timeout: float = 5) -> int:
    """Block until the 'Discard pending decisions?' QMessageBox appears.
    Returns the prompt window's hwnd. Raises TimeoutError on miss."""
    return _uia.wait_for_dialog(pid, PROMPT_TITLE, timeout=timeout)


def _click_prompt_button(prompt_hwnd: int, button_title: str) -> None:
    """Click Yes or No on the open prompt and wait for it to dismiss."""
    prompt = _uia.connect_by_handle(prompt_hwnd)
    _uia._focus(prompt)
    time.sleep(0.2)
    btn = prompt.child_window(title=button_title, control_type="Button")
    btn.click_input()
    time.sleep(0.4)


def _scan_dialog_log_text(scan_dlg) -> str:
    """Return current text content of the ScanDialog log widget."""
    log_edit = scan_dlg.child_window(
        auto_id=_uia.SCAN_AID_LOG, control_type="Edit"
    )
    return log_edit.window_text() or ""


def main() -> int:
    print("scenario: s27_rescan_confirm")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

    # ── Initial scan + load ──────────────────────────────────────────────
    print("step: initial_scan")
    dlg, _ = _uia.open_scan_dialog(win)
    print(f"  configured_sources={_uia.read_configured_sources(dlg)!r}")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=30)
    print(f"  scan_elapsed_s={elapsed:.2f}")
    _uia.close_and_load_manifest(dlg)
    _, win = _uia.connect_main()

    if _read_decision_count() != 0:
        print(f"FAIL: post-load decision count expected 0, got {_read_decision_count()}")
        return 1

    # ── Set a decision on row 0 ───────────────────────────────────────────
    print("step: set_first_decision")
    print(f"  target={ROW_FIRST!r} action=delete")
    _uia.left_click_tree_row(win, ROW_FIRST)
    _uia.right_click_tree_row(win, ROW_FIRST)
    _uia.select_popup_menu_path(pid, [_uia.CTX_SET_ACTION, _uia.CTX_DELETE])
    decisions_before_branch_a = _read_decision_count()
    print(f"  decision_count={decisions_before_branch_a}")
    if decisions_before_branch_a != 1:
        print(f"FAIL: expected 1 pending decision, got {decisions_before_branch_a}")
        return 1

    failures: list[str] = []

    # ── Branch A: prompt appears, click No, scan does NOT run ────────────
    print("step: branch_a_cancel_via_no")
    dlg_a, _ = _uia.open_scan_dialog(win)
    # Click Start Scan — expect prompt rather than a scan kicking off.
    start_btn = dlg_a.child_window(
        title=_uia.SCAN_BTN_START, control_type="Button"
    )
    _uia._focus(dlg_a)
    start_btn.invoke()
    try:
        prompt_hwnd = _wait_for_prompt(pid, timeout=5)
        prompt = _uia.connect_by_handle(prompt_hwnd)
        # Verify Yes and No buttons both exist (shape check).
        btn_titles = sorted(
            (b.window_text() or "").strip()
            for b in prompt.descendants(control_type="Button")
            if (b.window_text() or "").strip() in {"Yes", "No"}
        )
        print(f"  prompt_buttons={btn_titles}")
        if btn_titles != ["No", "Yes"]:
            failures.append(
                f"branch_a: prompt buttons mismatch — expected [No, Yes], "
                f"got {btn_titles}"
            )
        # Verify the body mentions the count.
        body_texts = [
            (t.window_text() or "")
            for t in prompt.descendants(control_type="Text")
        ]
        full_body = " ".join(body_texts)
        if "1 pending decision" not in full_body:
            failures.append(
                f"branch_a: prompt body missing pluralised count; "
                f"body={full_body!r}"
            )
        else:
            print(f"  body_includes_count=True")
    except TimeoutError:
        failures.append("branch_a: prompt did not appear within 5s of Start Scan")
        prompt_hwnd = None

    if prompt_hwnd is not None:
        _click_prompt_button(prompt_hwnd, "No")
        # Scan dialog should remain open with empty log (no scan ran).
        time.sleep(0.5)
        log_text = _scan_dialog_log_text(dlg_a)
        print(f"  log_after_no_len={len(log_text)}")
        if "Done." in log_text or "Indexed in manifest" in log_text:
            failures.append(
                f"branch_a: scan appears to have run despite No click; "
                f"log fragment: {log_text[:200]!r}"
            )
        decisions_after_no = _read_decision_count()
        print(f"  decisions_after_no={decisions_after_no}")
        if decisions_after_no != 1:
            failures.append(
                f"branch_a: decision count changed after No click; "
                f"expected 1, got {decisions_after_no}"
            )

    # Close the scan dialog (still open; click the regular Close).
    print("step: close_scan_dialog_after_branch_a")
    _uia.close_scan_dialog_via_close_button(dlg_a)
    time.sleep(0.5)
    _, win = _uia.connect_main()

    # ── Branch B: prompt appears, click Yes, scan runs ────────────────────
    print("step: branch_b_proceed_via_yes")
    # Add another decision to make the test more realistic.
    print(f"  set additional decision on {ROW_SECOND!r}")
    _uia.left_click_tree_row(win, ROW_SECOND)
    _uia.right_click_tree_row(win, ROW_SECOND)
    _uia.select_popup_menu_path(pid, [_uia.CTX_SET_ACTION, _uia.CTX_DELETE])
    decisions_before_branch_b = _read_decision_count()
    print(f"  decision_count_before_branch_b={decisions_before_branch_b}")
    if decisions_before_branch_b != 2:
        failures.append(
            f"branch_b setup: expected 2 pending decisions, "
            f"got {decisions_before_branch_b}"
        )

    dlg_b, _ = _uia.open_scan_dialog(win)
    start_btn = dlg_b.child_window(
        title=_uia.SCAN_BTN_START, control_type="Button"
    )
    _uia._focus(dlg_b)
    start_btn.invoke()
    try:
        prompt_hwnd_b = _wait_for_prompt(pid, timeout=5)
        prompt_b = _uia.connect_by_handle(prompt_hwnd_b)
        # Body should now reflect the higher count.
        body_texts_b = [
            (t.window_text() or "")
            for t in prompt_b.descendants(control_type="Text")
        ]
        full_body_b = " ".join(body_texts_b)
        if "2 pending decisions" not in full_body_b:
            failures.append(
                f"branch_b: prompt body should pluralise to '2 pending "
                f"decisions'; body={full_body_b!r}"
            )
        else:
            print(f"  body_includes_plural_count=True")
        _click_prompt_button(prompt_hwnd_b, "Yes")
    except TimeoutError:
        failures.append("branch_b: prompt did not appear within 5s of Start Scan")

    # After Yes, the scan should run. Wait for the log to show "Done."
    # via the same helper used by run_scan_and_wait.
    print("step: wait_for_scan_completion_after_yes")
    log_edit = dlg_b.child_window(
        auto_id=_uia.SCAN_AID_LOG, control_type="Edit"
    )
    try:
        _uia.wait_for_text_in(log_edit, ["Done.", "Error", "Failed"], timeout=30)
        print("  scan_ran_after_yes=True")
    except TimeoutError:
        failures.append("branch_b: scan did not complete within 30s of Yes click")

    # Manifest got rebuilt → all decisions cleared (the destructive
    # behaviour the user just opted in to).
    decisions_after_yes = _read_decision_count()
    print(f"  decisions_after_yes={decisions_after_yes}")
    if decisions_after_yes != 0:
        failures.append(
            f"branch_b: re-scan should reset decisions to 0 (manifest "
            f"replaced), got {decisions_after_yes}"
        )

    # Close the dialog cleanly via Close & Load to leave the app in a
    # tidy state for the next batch scenario.
    try:
        _uia.close_and_load_manifest(dlg_b)
    except Exception as exc:
        print(f"  cleanup close_and_load: {exc!r}")

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("scenario: s27_rescan_confirm DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
