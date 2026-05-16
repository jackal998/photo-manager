"""Scenario 53 — Set Action by Field/Regex: multi-condition OR (#173).

Required source: qa/sandbox/near-duplicates (5 JPEGs —
neardup_{00..04}_q{95,88,80,72,65}.jpg).

Sister to s52 (AND combinator). This scenario drives the OR side of
the Phase D multi-condition Apply path:

  1. Scan → close & load.
  2. Action menu → Set Action by Field/Regex…
  3. Fill row 0: Regex mode, Field=File Name, regex ``q[89]\\d``
     — matches q95, q88, q80 (3 of 5).
  4. Click "+ Add condition" — second row + combinator picker.
  5. Fill extra row: Field=File Name, Simple "contains" text ``q6``
     — matches q65 (1 of 5). Disjoint from row 0's matches.
  6. **Switch combinator from ALL → ANY (OR)** via the combinator combo.
  7. Set Action=delete, Apply, Close.
  8. Verify: the OR union (q95, q88, q80, q65 — 4 of 5) carries
     user_decision='delete'; q72 stays unchanged.

This pins:
  * The combinator combo's ANY selection actually flips the matcher
    from AND to OR end-to-end (not just visually).
  * Disjoint conditions still combine correctly — OR widens the
    affected set beyond what either condition would have selected
    on its own.

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
FIXTURE_NAME_GLOB = "neardup_"

ROW0_FIELD = "File Name"
ROW0_REGEX = r"q[89]\d"  # → q95, q88, q80

EXTRA_FIELD = "File Name"
EXTRA_SIMPLE_TEXT = "q6"  # → q65 (Simple "contains" synthesises regex "q6")

# i18n label for the OR (ANY) combinator item. Both en and zh_TW have
# the matching multi.combinator_any string — we match exact text from
# the en label here (qa runs default to en).
COMBINATOR_ANY_LABEL = "ANY"

ACTION_LABEL = "delete"


def _read_decisions() -> dict[str, str]:
    if not MANIFEST_PATH.exists():
        raise RuntimeError(f"manifest not found at {MANIFEST_PATH}")
    conn = sqlite3.connect(str(MANIFEST_PATH))
    try:
        rows = conn.execute(
            "SELECT source_path, user_decision FROM migration_manifest "
            "WHERE source_path LIKE ?",
            (f"%{FIXTURE_NAME_GLOB}%",),
        ).fetchall()
    finally:
        conn.close()
    return {Path(p).name: (d or "") for p, d in rows}


def main() -> int:
    print("scenario: s53_multi_condition_or")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

    print("step: open_scan_dialog")
    dlg, _ = _uia.open_scan_dialog(win)

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=30)
    print(f"  scan_elapsed_s={elapsed:.2f}")

    print("step: close_dialog")
    _uia.close_and_load_manifest(dlg)
    _, win = _uia.connect_main()

    print("step: snapshot_pre_decisions")
    pre = _read_decisions()
    if not pre:
        print("FAIL: no fixture rows found in manifest after scan")
        return 1

    print("step: open_action_dialog_via_menu")
    _uia.menu_path(win, _uia.MENU_ACTION, _uia.ACTION_BY_REGEX)
    action_hwnd = _uia.wait_for_dialog(
        pid, _uia.ACTION_DIALOG_TITLE, timeout=5,
    )
    action_dlg = _uia.connect_by_handle(action_hwnd)
    _uia._focus(action_dlg)
    time.sleep(0.3)

    print("step: row0_switch_to_regex_mode")
    for radio in action_dlg.descendants(control_type="RadioButton"):
        try:
            aid = radio.element_info.automation_id or ""
        except Exception:
            aid = ""
        if aid.endswith(".regexModeRegex"):
            try:
                if not radio.is_selected():
                    radio.click_input()
                    time.sleep(0.2)
            except Exception:
                pass
            break

    print(f"step: row0_fill field={ROW0_FIELD!r} regex={ROW0_REGEX!r}")
    field_combo = _uia._find_descendant_by_aid_suffix(
        action_dlg, "ComboBox", ".regexFieldCombo"
    )
    if field_combo is None:
        print("FAIL: regexFieldCombo not found")
        return 1
    field_combo.select(ROW0_FIELD)
    time.sleep(0.1)
    regex_edit = _uia._find_descendant_by_aid_suffix(
        action_dlg, "Edit", ".regexLineEdit"
    )
    if regex_edit is None:
        print("FAIL: regexLineEdit not found")
        return 1
    regex_edit.iface_value.SetValue(ROW0_REGEX)
    time.sleep(0.3)

    print("step: click_add_condition")
    add_btn = _uia._find_descendant_by_aid_suffix(
        action_dlg, "Button", ".addConditionButton"
    )
    if add_btn is None:
        print("FAIL: addConditionButton not found")
        return 1
    add_btn.click_input()
    time.sleep(0.3)

    print(f"step: extra_row_fill field={EXTRA_FIELD!r} simple={EXTRA_SIMPLE_TEXT!r}")
    extra_field_combo = _uia._find_descendant_by_aid_suffix(
        action_dlg, "ComboBox", "extraConditionRow0.fieldCombo"
    )
    if extra_field_combo is None:
        print("FAIL: extraConditionRow0.fieldCombo not found")
        return 1
    extra_field_combo.select(EXTRA_FIELD)
    time.sleep(0.1)
    extra_simple_text = _uia._find_descendant_by_aid_suffix(
        action_dlg, "Edit", "extraConditionRow0.simpleText"
    )
    if extra_simple_text is None:
        print("FAIL: extraConditionRow0.simpleText not found")
        return 1
    extra_simple_text.iface_value.SetValue(EXTRA_SIMPLE_TEXT)
    time.sleep(0.3)

    print(f"step: switch_combinator_to_any label={COMBINATOR_ANY_LABEL!r}")
    combinator_combo = _uia._find_descendant_by_aid_suffix(
        action_dlg, "ComboBox", ".combinatorCombo"
    )
    if combinator_combo is None:
        print("FAIL: combinatorCombo not found after adding extra row")
        return 1
    # Retry-with-verify pattern mirrors _drive_action_dialog_form's
    # handling of pywinauto's flaky non-default-item selects.
    for attempt in range(3):
        try:
            combinator_combo.set_focus()
        except Exception:
            pass
        time.sleep(0.1)
        try:
            combinator_combo.select(COMBINATOR_ANY_LABEL)
        except Exception:
            pass
        time.sleep(0.3)
        try:
            current = (combinator_combo.window_text() or "").strip()
        except Exception:
            current = ""
        if current == COMBINATOR_ANY_LABEL:
            break
    print(f"  combinator_current_text={current!r}")

    print("step: set_action_delete")
    action_combo = _uia._find_descendant_by_aid_suffix(
        action_dlg, "ComboBox", ".regexActionCombo"
    )
    if action_combo is None:
        print("FAIL: regexActionCombo not found")
        return 1
    for attempt in range(3):
        try:
            action_combo.set_focus()
        except Exception:
            pass
        time.sleep(0.1)
        try:
            action_combo.select(ACTION_LABEL)
        except Exception:
            pass
        time.sleep(0.4)
        try:
            current_act = (action_combo.window_text() or "").strip()
        except Exception:
            current_act = ""
        if current_act == ACTION_LABEL:
            break

    print("step: click_apply")
    apply_btn = _uia._find_dialog_button(
        action_dlg, _uia.ACTION_DIALOG_BTN_APPLY
    )
    apply_btn.click_input()
    time.sleep(0.4)

    print("step: close_dialog")
    close_btn = _uia._find_dialog_button(
        action_dlg, _uia.ACTION_DIALOG_BTN_CLOSE
    )
    close_btn.click_input()
    time.sleep(0.3)

    print("step: verify_decisions_after_apply")
    post = _read_decisions()
    # OR union: q95, q88, q80 (row 0 matches) + q65 (extra matches).
    # q72 matches neither → unchanged.
    expected_delete = sorted(
        name for name in post
        if any(tok in name for tok in ("q95", "q88", "q80", "q65"))
    )
    expected_unchanged = sorted(
        name for name in post if "q72" in name
    )

    failures: list[str] = []
    for name in expected_delete:
        if post[name] != "delete":
            failures.append(f"{name}: expected 'delete', got {post[name]!r}")
    for name in expected_unchanged:
        if post[name] != pre[name]:
            failures.append(
                f"{name}: expected unchanged ({pre[name]!r}), got {post[name]!r}"
            )

    print(f"  expected_delete={expected_delete}")
    print(f"  expected_unchanged={expected_unchanged}")
    for name in sorted(post):
        print(f"  row: name={name} pre={pre[name]!r} post={post[name]!r}")

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("scenario: s53_multi_condition_or DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
