"""Scenario 43 — Set Action dialog: numeric threshold condition (#209).

Required source: qa/sandbox/near-duplicates (5 JPEG re-saves at qualities
95/88/80/72/65 from one base image). The file sizes are well-separated
across the quality ladder, so a Size (Bytes) threshold cleanly partitions
the group.

What this exercises (the production wiring that layer-1 unit tests
can't reach):

  1. Execute Action dialog passes `groups=` to ActionDialog so the
     numeric-condition panel is reachable from the Select by Field/Regex
     route (#209 — `groups=self._groups` in execute_action_dialog.py).
  2. ActionDialog swaps the regex/simple panel for the numeric panel
     when the user picks a numeric-capable field (Size (Bytes)).
  3. Threshold mode encodes `__cmp__:OP:VALUE` into the
     setActionRequested signal.
  4. The downstream `_set_decision_by_regex` recognises the
     `__cmp__:` prefix, decodes it, runs `select_paths_by_threshold`
     against the live groups, and applies the decision via the same
     batch-write path the regex flow uses.
  5. Per-file manifest write goes through (ManifestRepository
     batch_update_decisions) so the decisions survive a re-load.

Non-destructive: Apply within the Set Action dialog only sets
`user_decision` on the matched rows; it does NOT delete files. The
scenario closes the Execute Action dialog via Cancel after verification,
so nothing is actually sent to the recycle bin.

PRE: qa/sandbox/near-duplicates must be a configured scan source —
this is the project default. PHOTO_MANAGER_HOME=qa
QT_ACCESSIBILITY=1 .venv/Scripts/python.exe main.py.
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"
FIXTURE_DIR = REPO / "qa" / "sandbox" / "near-duplicates"

# Internal field name carried through ActionDialog → handler. Display
# label happens to match in en.yml; if it ever diverges, this needs to
# be the LOCALIZED label (the UIA helper drives the combo by visible text).
SIZE_FIELD = "Size (Bytes)"


def _read_fixture_decisions() -> dict[str, dict]:
    """Return {basename: {decision, size}} for the near-duplicates rows."""
    if not MANIFEST_PATH.exists():
        raise RuntimeError(f"manifest not found at {MANIFEST_PATH}")
    conn = sqlite3.connect(str(MANIFEST_PATH))
    try:
        rows = conn.execute(
            "SELECT source_path, user_decision, file_size_bytes "
            "FROM migration_manifest WHERE source_path LIKE ?",
            ("%near-duplicates%neardup_%",),
        ).fetchall()
    finally:
        conn.close()
    return {
        Path(p).name: {"decision": d or "", "size": int(s)}
        for p, d, s in rows
    }


def _reset_fixture_decisions() -> None:
    """Clear `user_decision` for the fixture rows so the scenario can
    re-run without inherited decisions skewing the pre-snapshot."""
    if not MANIFEST_PATH.exists():
        return
    conn = sqlite3.connect(str(MANIFEST_PATH))
    try:
        conn.execute(
            "UPDATE migration_manifest SET user_decision='' "
            "WHERE source_path LIKE ?",
            ("%near-duplicates%neardup_%",),
        )
        conn.commit()
    finally:
        conn.close()


def _drive_numeric_threshold(
    execute_dlg, *, field: str, op: str, value_text: str, action_label: str
) -> str | None:
    """Open the Set Action dialog from within Execute Action, switch
    to a numeric field, set a threshold condition, click Apply, then
    Close. Returns the live-preview match-counter text or None.

    Mirrors _uia.mark_all_via_regex in shape but drives the new
    numeric panel widgets (objectNames: numericCmpCombo,
    numericValueEdit) instead of the regex line edit.
    """
    pid = execute_dlg.process_id()
    select_btn = execute_dlg.child_window(
        title=_uia.EXECUTE_BTN_SELECT_BY_REGEX, control_type="Button"
    )
    action_hwnd = _uia._click_btn_and_wait_for_dialog(
        select_btn, execute_dlg, pid, _uia.ACTION_DIALOG_TITLE,
    )
    action_dlg = _uia.connect_by_handle(action_hwnd)
    _uia._focus(action_dlg)
    time.sleep(0.3)

    # Step 1 — pick the numeric field via the field combo. The dialog's
    # _on_field_changed handler swaps panel visibility.
    field_combo = _uia._find_descendant_by_aid_suffix(
        action_dlg, "ComboBox", ".regexFieldCombo"
    )
    if field_combo is None:
        raise RuntimeError("action dialog: regexFieldCombo not found")
    field_combo.select(field)
    time.sleep(0.2)

    # Step 2 — verify the numeric value-edit exists and is reachable.
    # If the field-changed handler didn't swap panels, this lookup
    # will fail rather than driving the wrong widget.
    value_edit = _uia._find_descendant_by_aid_suffix(
        action_dlg, "Edit", ".numericValueEdit"
    )
    if value_edit is None:
        raise RuntimeError(
            "action dialog: numericValueEdit not found — numeric panel "
            "did not surface for field={!r}".format(field)
        )

    op_combo = _uia._find_descendant_by_aid_suffix(
        action_dlg, "ComboBox", ".numericCmpCombo"
    )
    if op_combo is None:
        raise RuntimeError("action dialog: numericCmpCombo not found")
    op_combo.select(op)
    time.sleep(0.1)

    # Step 3 — set threshold value. SetValue bypasses IME interception
    # the same way the regex line edit does for the existing flows.
    value_edit.iface_value.SetValue(str(value_text))
    time.sleep(0.3)  # past the live-preview debounce

    # Step 4 — pick the Set Action and Apply. Reusing the regex flow's
    # action combo lookup because the same widget is shared.
    action_combo = _uia._find_descendant_by_aid_suffix(
        action_dlg, "ComboBox", ".regexActionCombo"
    )
    if action_combo is None:
        raise RuntimeError("action dialog: regexActionCombo not found")
    # Use the same retry loop as _drive_action_dialog_form — same
    # combo-flake mode applies.
    for _attempt in range(3):
        try:
            action_combo.set_focus()
        except Exception:
            pass
        time.sleep(0.1)
        try:
            action_combo.select(action_label)
        except Exception:
            pass
        time.sleep(0.4)
        try:
            current = (action_combo.window_text() or "").strip()
        except Exception:
            current = ""
        if current == action_label:
            break

    apply_btn = _uia._find_dialog_button(action_dlg, _uia.ACTION_DIALOG_BTN_APPLY)
    apply_btn.click_input()
    time.sleep(0.5)

    # Read the live counter AFTER Apply (same reason as regex flow —
    # Apply doesn't dismiss).
    counter_text: str | None = None
    counter = _uia._find_descendant_by_aid_suffix(
        action_dlg, "Text", ".regexMatchCounter"
    )
    if counter is not None:
        try:
            counter_text = counter.window_text() or None
        except Exception:
            counter_text = None

    close_btn = _uia._find_dialog_button(action_dlg, _uia.ACTION_DIALOG_BTN_CLOSE)
    close_btn.click_input()
    time.sleep(0.3)
    return counter_text


def main() -> int:
    print("scenario: s43_numeric_condition")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

    # 1. Scan the fixture so decisions are reachable through the
    # Execute Action dialog.
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

    # 2. Reset any inherited decisions from a prior run, then capture
    # the pre-action snapshot — file sizes drive threshold selection.
    print("step: reset_and_snapshot_pre")
    _reset_fixture_decisions()
    pre = _read_fixture_decisions()
    if len(pre) != 5:
        print(f"FAIL: expected 5 fixture rows, got {len(pre)} — fixture or scan misconfigured")
        return 1
    for name in sorted(pre):
        print(f"  pre row: {name} size={pre[name]['size']} decision={pre[name]['decision']!r}")

    # Pick threshold = (size of q72) so "> threshold" selects q95, q88, q80.
    # q72 is the second-smallest; the threshold splits the 5 cleanly into
    # 3 matched + 2 unchanged. Using a runtime-computed threshold means
    # the scenario survives the fixture being regenerated with different
    # absolute sizes (only the relative ordering matters).
    sizes_sorted = sorted(pre.items(), key=lambda kv: kv[1]["size"])
    boundary = sizes_sorted[1][1]["size"]
    expected_match = {name for name, info in pre.items() if info["size"] > boundary}
    expected_unchanged = set(pre) - expected_match
    print(f"  boundary={boundary} (size of {sizes_sorted[1][0]})")
    print(f"  expected_match={sorted(expected_match)}")
    print(f"  expected_unchanged={sorted(expected_unchanged)}")
    if len(expected_match) != 3 or len(expected_unchanged) != 2:
        print(
            f"FAIL: fixture sizes don't produce the expected 3-vs-2 split — "
            f"sorted={sizes_sorted!r}"
        )
        return 1

    # 3. Drive the Execute Action dialog → Set Action → numeric threshold.
    print("step: open_execute_dialog")
    exec_dlg, _ = _uia.open_execute_action_dialog(win)

    print("step: apply_numeric_threshold")
    counter_text = _drive_numeric_threshold(
        exec_dlg,
        field=SIZE_FIELD,
        op=">",
        value_text=boundary,
        action_label="delete",
    )
    print(f"  counter_text={counter_text!r}")
    if counter_text is None:
        print("FAIL: live-preview counter not found — preview pane missing?")
        return 1
    # Counter should mention 3 matches and 5 total (digit presence is enough —
    # exact format is locale-dependent).
    if "3" not in counter_text or "5" not in counter_text:
        print(
            f"FAIL: counter text {counter_text!r} should mention "
            f"3 matched and 5 total"
        )
        # Don't return immediately — manifest check below is the
        # load-bearing assertion.

    # 4. Cancel the Execute Action dialog so no file deletions happen.
    # The decisions have already been persisted by Apply via
    # ManifestRepository.batch_update_decisions, so cancelling just
    # closes the review without firing send2trash.
    print("step: cancel_execute_dialog")
    close_btn = _uia._find_dialog_button(exec_dlg, "Close")
    close_btn.click_input()
    time.sleep(0.3)

    # 5. Verify the manifest reflects the threshold's split.
    print("step: verify_decisions_after_apply")
    post = _read_fixture_decisions()
    failures: list[str] = []
    for name in expected_match:
        if post[name]["decision"] != "delete":
            failures.append(
                f"{name}: expected 'delete', got {post[name]['decision']!r}"
            )
    for name in expected_unchanged:
        if post[name]["decision"] != "":
            failures.append(
                f"{name}: expected '' (unchanged), got {post[name]['decision']!r}"
            )
    for name in sorted(post):
        print(
            f"  post row: {name} size={post[name]['size']} "
            f"decision={post[name]['decision']!r}"
        )

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    # 6. Cleanup — reset decisions so the next scenario doesn't
    # inherit the delete marks.
    print("step: cleanup_reset_decisions")
    _reset_fixture_decisions()

    print("scenario: s43_numeric_condition DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
