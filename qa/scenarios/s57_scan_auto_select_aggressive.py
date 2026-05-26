"""Scenario 57 — auto-select aggressive mode (#393).

Companion to s49 (which pins the non-aggressive keep+lock flow). This
scenario exercises the opt-in "Also mark all other files for delete"
sub-option:

  1. The aggressive sub-checkbox lives in ScanDialog's Advanced
     Settings, indented beneath the parent "Auto select after scan".
  2. Enabling the parent enables the sub; toggling the sub on persists
     ``ui.scan_dialog.auto_select_aggressive_delete=True`` and passes
     the flag through to ScanWorker.
  3. On scan completion the keeper row gets ``user_decision=""``
     (canonical empty keep — #425; was the literal ``"keep"`` before)
     AND ``is_locked=1`` (the s49 contract); ADDITIONALLY every
     non-keeper row in a scored group receives
     ``user_decision='delete'`` so the user opens Execute Action with
     the full triage pre-populated. The aggressive write does NOT
     lock non-keepers — they must remain editable through the
     standard review flow.

Why two scenarios instead of folding into s49: the aggressive flow
toggles a second checkbox AND re-runs the scan, which roughly doubles
s49's runtime. Splitting keeps each scenario focused on one contract
and lets the qa-batch shard them independently.

Non-destructive: no files are moved or deleted. The aggressive flag
only writes ``user_decision`` to the manifest; the actual delete
requires the user to click Execute Action.

PRE: PHOTO_MANAGER_HOME=qa QT_ACCESSIBILITY=1 .venv/Scripts/python.exe main.py
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"

# UIA accessible names — must match translations/en.yml.
AUTO_SELECT_CHECKBOX_TITLE = "Auto select after scan"
AGGRESSIVE_CHECKBOX_TITLE = "Also mark all other files for delete"
ADVANCED_GROUP_TITLE = "Advanced settings"

# Per-#187 the near-duplicates fixture's q95 file is the highest-scoring
# row — the keeper auto-select picks. The other four are non-keepers in
# a scored group, so aggressive mode tags them all 'delete'.
EXPECTED_KEEPER = "neardup_00_q95.jpg"
EXPECTED_NON_KEEPERS = {
    "neardup_01_q88.jpg",
    "neardup_02_q80.jpg",
    "neardup_03_q72.jpg",
    "neardup_04_q65.jpg",
}


def _read_manifest_state() -> dict[str, tuple[str, int]]:
    """Return ``{basename: (user_decision, is_locked)}`` for the near-dup rows."""
    if not MANIFEST_PATH.exists():
        raise RuntimeError(f"manifest not found at {MANIFEST_PATH}")
    conn = sqlite3.connect(str(MANIFEST_PATH))
    try:
        rows = conn.execute(
            "SELECT source_path, user_decision, is_locked "
            "FROM migration_manifest "
            "WHERE source_path LIKE ?",
            ("%near-duplicates%neardup_%",),
        ).fetchall()
    finally:
        conn.close()
    return {Path(p).name: (ud or "", lk) for p, ud, lk in rows}


def _click_advanced_group(dlg) -> None:
    grp = dlg.child_window(
        title=ADVANCED_GROUP_TITLE, control_type="CheckBox"
    )
    grp.toggle()
    time.sleep(0.2)


def _toggle_checkbox(dlg, title: str) -> None:
    cb = dlg.child_window(title=title, control_type="CheckBox")
    cb.toggle()
    time.sleep(0.2)


def _is_checkbox_checked(dlg, title: str) -> bool:
    cb = dlg.child_window(title=title, control_type="CheckBox")
    return cb.get_toggle_state() == 1


def main() -> int:
    print("scenario: s57_scan_auto_select_aggressive")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

    failures: list[str] = []

    print("step: open_scan_dialog")
    dlg, _ = _uia.open_scan_dialog(win)

    print("step: expand_advanced_settings")
    _click_advanced_group(dlg)

    print("step: assert_aggressive_default_off")
    if _is_checkbox_checked(dlg, AGGRESSIVE_CHECKBOX_TITLE):
        failures.append(
            "aggressive checkbox starts ON; default must be OFF "
            "(destructive-leaning, opt-in only)"
        )
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("step: enable_parent_auto_select")
    _toggle_checkbox(dlg, AUTO_SELECT_CHECKBOX_TITLE)
    if not _is_checkbox_checked(dlg, AUTO_SELECT_CHECKBOX_TITLE):
        failures.append("parent auto-select did not turn on after toggle")
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("step: enable_aggressive_subcheckbox")
    _toggle_checkbox(dlg, AGGRESSIVE_CHECKBOX_TITLE)
    if not _is_checkbox_checked(dlg, AGGRESSIVE_CHECKBOX_TITLE):
        failures.append(
            "aggressive sub-checkbox did not turn on after toggle — "
            "wiring or gating regression"
        )
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=60)
    print(f"  scan_elapsed_s={elapsed:.2f}")
    if "Auto-select aggressive" not in log:
        # Worker emits a one-liner naming the count of deletes; absence
        # means the aggressive flag did not flow through to the worker
        # even though the checkbox was on at scan time.
        failures.append(
            "scan log missing 'Auto-select aggressive' line — "
            "aggressive branch did not fire"
        )

    # See s49 — same race window between "Done." and the worker's
    # finished signal being dispatched on the main thread.
    time.sleep(1.0)

    print("step: close_and_load")
    _uia.close_and_load_manifest(dlg)

    print("step: verify_manifest")
    state = _read_manifest_state()
    print(f"  manifest_state={dict(sorted(state.items()))}")

    if EXPECTED_KEEPER not in state:
        failures.append(
            f"expected keeper {EXPECTED_KEEPER!r} missing from manifest"
        )
    else:
        ud, lk = state[EXPECTED_KEEPER]
        # #425 — canonical empty keep; was "keep" literal pre-fix.
        if ud != "":
            failures.append(
                f"{EXPECTED_KEEPER}.user_decision={ud!r}, expected "
                f"'' (canonical keep state — #425; #393 keep+lock "
                f"write missing or non-canonical)"
            )
        if lk != 1:
            failures.append(
                f"{EXPECTED_KEEPER}.is_locked={lk!r}, expected 1 "
                f"(#393 keep+lock write missing — no tree badge)"
            )

    # Every non-keeper row in the scored group MUST receive
    # user_decision='delete' but NOT is_locked=1 — locking them would
    # block the standard Execute Action confirmation flow.
    for name in EXPECTED_NON_KEEPERS:
        if name not in state:
            failures.append(
                f"expected non-keeper {name!r} missing from manifest"
            )
            continue
        ud, lk = state[name]
        if ud != "delete":
            failures.append(
                f"non-keeper {name}.user_decision={ud!r}, expected "
                f"'delete' (aggressive mode must tag every non-keeper "
                f"in a scored group)"
            )
        if lk != 0:
            failures.append(
                f"non-keeper {name}.is_locked={lk!r}, expected 0 "
                f"(aggressive mode must NOT lock non-keepers — they "
                f"need to remain editable through the standard flow)"
            )

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("scenario: s57_scan_auto_select_aggressive DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
