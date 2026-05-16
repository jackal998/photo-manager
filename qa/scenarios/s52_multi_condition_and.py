"""Scenario 52 — Set Action by Field/Regex: multi-condition AND (#173).

Required source: qa/sandbox/near-duplicates (5 JPEGs —
neardup_{00..04}_q{95,88,80,72,65}.jpg).

Drives the Phase D multi-condition Apply path:

  1. Scan → close & load.
  2. Action menu → Set Action by Field/Regex…
  3. Fill row 0: switch to Regex mode, Field=File Name, regex ``q[89]\\d``
     — matches q95, q88, q80 (3 of 5).
  4. Click "+ Add condition" — second row appears, combinator picker
     surfaces.
  5. Fill extra row: Field=File Name, Simple "contains" text ``q8``
     — matches q88, q80 (2 of 5).
  6. Leave combinator at default ALL (AND).
  7. Set Action=delete, Apply, Close.
  8. Verify: ONLY the AND-intersection rows (q88, q80) carry
     user_decision='delete' in the manifest; the rest unchanged.

This pins:
  * The combinator picker is hidden at N=1 and visible after the
    first ``+ Add condition`` click.
  * AND short-circuits correctly — q95 matches only cond A so it
    stays untouched even though A alone would have deleted it.
  * Extra rows in Simple mode synthesise the same regex the dialog
    would have built from row 0 in Simple mode (Simple "contains q8"
    → ``q8`` → matches q88 + q80).

Sister scenario: s53 (same fixture, OR combinator, different
conditions). The two together cover the AND/OR axis at the qa-explore
layer; unit tests in ``tests/test_file_operations.py``
(``TestBuildMatchFnConditions``) and ``tests/test_select_dialog.py``
(``TestMultiCondition``) cover the matrix at layer 1.

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

# Row 0 condition: case-insensitive regex matching qXX where X in {8,9}.
# This is the existing single-row Regex-mode path — the multi-condition
# additions must not regress it.
ROW0_FIELD = "File Name"
ROW0_REGEX = r"q[89]\d"

# Extra row condition: Simple "contains q8" — synthesises regex "q8"
# inside the row widget, narrowing the AND result to the subset of
# row 0's matches that also contain "q8". q95 matches row 0 but NOT
# the extra; q88 + q80 match both.
EXTRA_FIELD = "File Name"
EXTRA_SIMPLE_TEXT = "q8"

ACTION_LABEL = "delete"


def _read_decisions() -> dict[str, str]:
    """Return {basename: user_decision} for every fixture row in the manifest."""
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
    print("scenario: s52_multi_condition_and")
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
    print(f"  pre_total={len(pre)}")

    print("step: open_action_dialog_via_menu")
    _uia.menu_path(win, _uia.MENU_ACTION, _uia.ACTION_BY_REGEX)
    action_hwnd = _uia.wait_for_dialog(
        pid, _uia.ACTION_DIALOG_TITLE, timeout=5,
    )
    action_dlg = _uia.connect_by_handle(action_hwnd)
    _uia._focus(action_dlg)
    time.sleep(0.3)

    # Switch row 0 to Regex mode so the regex line edit drives the
    # first condition (Simple mode would re.escape() the pattern).
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
        print("FAIL: regexFieldCombo not found in action dialog")
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

    # Verify combinator picker is HIDDEN before adding the extra row
    # (N=1 layout pin).
    combinator_combo = _uia._find_descendant_by_aid_suffix(
        action_dlg, "ComboBox", ".combinatorCombo"
    )
    if combinator_combo is not None:
        try:
            if combinator_combo.is_visible():
                print(
                    "FAIL: combinatorCombo is visible before adding extra "
                    "row — N=1 layout regression."
                )
                return 1
        except Exception:
            pass

    print("step: click_add_condition")
    add_btn = _uia._find_descendant_by_aid_suffix(
        action_dlg, "Button", ".addConditionButton"
    )
    if add_btn is None:
        print("FAIL: addConditionButton not found — Phase D scaffolding missing?")
        return 1
    add_btn.click_input()
    time.sleep(0.3)

    print("step: assert_combinator_visible_after_add")
    combinator_combo = _uia._find_descendant_by_aid_suffix(
        action_dlg, "ComboBox", ".combinatorCombo"
    )
    if combinator_combo is None or not bool(combinator_combo.is_visible()):
        print(
            "FAIL: combinatorCombo not visible after + Add condition — "
            "the N>=2 multi-condition layout did not surface."
        )
        return 1

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

    # Combinator default is ALL (AND). Leave it as-is — the test
    # verifies AND semantics specifically.

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
            current = (action_combo.window_text() or "").strip()
        except Exception:
            current = ""
        if current == ACTION_LABEL:
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
    # AND intersection: q88, q80. The fixture filenames embed the q-value
    # so we can identify them by substring.
    expected_delete = sorted(name for name in post if "q88" in name or "q80" in name)
    expected_unchanged = sorted(
        name for name in post if "q88" not in name and "q80" not in name
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

    deleted = sum(1 for n in expected_delete if post[n] == "delete")
    unchanged = sum(
        1 for n in expected_unchanged if post[n] == pre[n]
    )
    print(f"  expected_delete={expected_delete}")
    print(f"  expected_unchanged={expected_unchanged}")
    print(f"  deleted={deleted} unchanged={unchanged}")
    for name in sorted(post):
        print(f"  row: name={name} pre={pre[name]!r} post={post[name]!r}")

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("scenario: s52_multi_condition_and DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
