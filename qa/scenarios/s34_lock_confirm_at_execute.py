"""Scenario 34 — Execute-time lock confirm dialog (#182).

Required source: qa/sandbox/near-duplicates (5 files, basenames neardup_NN_qXX.jpg).

When the user has set ``decision='delete'`` on a row and THEN locked
it, clicking Execute must surface the unified LockedRowsConfirmDialog
BEFORE any destructive action runs. This pins the pre-execute scan in
``ExecuteActionDialog._on_execute_requested``: under the new lock
semantic (#182, supersedes #175) the dialog is the only path through
which locked rows can reach the recycle bin.

Non-destructive scenario — drives the **Cancel** verdict so no files
are actually deleted. The other two verdicts (Unlock & Apply All,
Apply to Unlocked Only) are pinned at layer 1 in
``tests/test_execute_action_dialog.py::TestExecuteRequestedLockConfirm``
where mocking the dialog avoids any real Execute pass.

Flow:
  scan → mark all 5 'delete' via regex → lock q95 via regex →
  open Execute Action → click Execute → lock-confirm fires (q95
  locked + decision='delete') → click Cancel → verify manifest
  unchanged + Execute Action still open → close.

Sister to s32 (bulk-regex trigger). Same fixture as s14 / s32 / s33.
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"
FIXTURE_NAME_GLOB = "neardup_"

ALL_REGEX = r"neardup_"          # matches every fixture row
LOCK_REGEX = r"q95"              # matches only neardup_00_q95
FIELD = "File Name"


def _read_state() -> dict[str, tuple[str, bool]]:
    """Return {basename: (user_decision, is_locked)} for every fixture row."""
    if not MANIFEST_PATH.exists():
        raise RuntimeError(f"manifest not found at {MANIFEST_PATH}")
    conn = sqlite3.connect(str(MANIFEST_PATH))
    try:
        rows = conn.execute(
            "SELECT source_path, user_decision, is_locked "
            "FROM migration_manifest WHERE source_path LIKE ?",
            (f"%{FIXTURE_NAME_GLOB}%",),
        ).fetchall()
    finally:
        conn.close()
    return {Path(p).name: ((d or ""), bool(loc)) for p, d, loc in rows}


def _print_state(label: str, state: dict[str, tuple[str, bool]]) -> None:
    for name in sorted(state):
        decision, locked = state[name]
        glyph = "🔒" if locked else "  "
        print(f"  {label}  {glyph} {name}  decision={decision!r}")


def main() -> int:
    print("scenario: s34_lock_confirm_at_execute")
    app, win = _uia.connect_main()
    print(f"connected: pid={win.process_id()} title={win.window_text()!r}")

    print("step: open_scan_dialog")
    dlg, _ = _uia.open_scan_dialog(win)

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=30)
    print(f"  scan_elapsed_s={elapsed:.2f}")

    print("step: close_dialog")
    _uia.close_and_load_manifest(dlg)
    _, win = _uia.connect_main()

    # ── Step A: mark every neardup row 'delete' (no locks yet → no confirm).
    print(f"step: bulk_delete regex={ALL_REGEX!r}")
    _uia.mark_all_via_regex_standalone(
        win, field=FIELD, regex=ALL_REGEX, action_label="delete"
    )
    after_delete = _read_state()
    not_deleted = [n for n, (d, _l) in after_delete.items() if d != "delete"]
    if not_deleted:
        print(f"FAIL: bulk delete did not cover every row; missed {not_deleted}")
        return 1

    # ── Step B: lock q95 AFTER its decision was set. Lock sentinel is free.
    print(f"step: lock_via_regex regex={LOCK_REGEX!r}")
    _uia.mark_all_via_regex_standalone(
        win, field=FIELD, regex=LOCK_REGEX, action_label="lock"
    )
    pre_execute = _read_state()
    _print_state("pre ", pre_execute)
    locked_with_delete = [
        n for n, (d, loc) in pre_execute.items() if loc and d == "delete"
    ]
    if locked_with_delete != ["neardup_00_q95.jpg"]:
        print(
            f"FAIL: expected exactly one locked-with-delete row "
            f"(neardup_00_q95.jpg); got {locked_with_delete}"
        )
        return 1

    # ── Step C: open Execute Action and click Execute. Pre-execute scan
    # finds q95 (locked + decision='delete') → lock-confirm fires. ───────
    print("step: open_execute_action_dialog")
    exec_dlg, _ = _uia.open_execute_action_dialog(win)
    pid = exec_dlg.process_id()

    print("step: click_execute_expecting_lock_confirm")
    execute_btn = exec_dlg.child_window(title=_uia.EXECUTE_BTN, control_type="Button")
    execute_btn.click_input()
    time.sleep(0.3)

    appeared = _uia.drive_lock_confirm(pid, _uia.LOCK_CONFIRM_CANCEL, timeout=5.0)
    if not appeared:
        print(
            "FAIL: LockedRowsConfirmDialog did not appear after Execute "
            "even though q95 is locked-with-decision='delete'"
        )
        return 1

    # After Cancel, _on_execute_requested returns without executing.
    # Manifest must match the pre-execute snapshot exactly.
    post_cancel = _read_state()
    _print_state("post", post_cancel)
    if post_cancel != pre_execute:
        print("FAIL: manifest changed after Cancel verdict; pre vs post diff:")
        for name in sorted(pre_execute):
            if pre_execute[name] != post_cancel[name]:
                print(f"  {name}: pre={pre_execute[name]} post={post_cancel[name]}")
        return 1

    # ── Step D: Execute Action dialog should still be on screen (Cancel
    # aborted the destructive op, didn't dismiss the review dialog). Close
    # it without executing. ─────────────────────────────────────────────
    print("step: close_execute_action_dialog")
    close_btn = _uia._find_dialog_button(exec_dlg, "Close")
    close_btn.click_input()
    time.sleep(0.3)

    print("scenario: s34_lock_confirm_at_execute DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
