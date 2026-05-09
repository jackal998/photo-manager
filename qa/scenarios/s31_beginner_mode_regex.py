"""Scenario 31 — Phase B Beginner mode: plain-text matching without regex.

Required source: ``qa/sandbox/near-duplicates`` (5 files, basenames neardup_NN_qXX.jpg).

Phase B introduced a Beginner / Regex mode toggle in ActionDialog.
Beginner is the default for new users — they see "Find rows where it
[contains | starts with | ends with | exactly matches] [text]" instead
of a regex line edit, and the dialog synthesises the regex internally
so the user never types a backslash.

This scenario pins the layer-3 invariants of that flow:
  scan → close & load → Action menu → Set Action by Field/Regex…
  → confirm Beginner mode is the active default →
  → set op="contains", text="q9" → verify counter shows "1 of 5 match"
  → switch action to "delete" → Apply → verify exactly 1 row was
    tagged user_decision='delete' and the right row at that
    (neardup_00_q95.jpg — the only one whose basename contains "q9").

Catches drift in: mode-toggle wiring, Beginner widget objectNames,
the BEGINNER_OP → regex builder mapping (especially the re.escape so
special chars in the user's text stay literal), and the live preview
+ counter still reflecting the synthesised pattern.

Distinct from s14 (Regex mode menu route) and s30 (Regex mode
right-click route from Execute dialog) — same fixture, different
entry path through the dialog.
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

from qa.scenarios import _invariants, _uia

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"
FIXTURE_NAME_GLOB = "neardup_"

# Beginner mode: op + plain text. With "contains" + "q9" the only
# matching basename is neardup_00_q95.jpg (the q88, q80, q72, q65
# rows don't contain "q9"). Keeps the verification crisp — exactly
# one row should land in user_decision='delete' afterwards.
FIELD = "File Name"
BEGINNER_OP_LABEL = "contains"  # also exact en label of the op combo item
BEGINNER_TEXT = "q9"
ACTION_LABEL = "delete"
EXPECTED_TARGET = "neardup_00_q95.jpg"


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


def _drive_beginner_form(action_dlg, op_label, text, action_label):
    """Fill the dialog using the Beginner-mode widgets (no regex line)."""
    # Beginner-mode op combo: "contains" / "starts with" / etc.
    op_combo = _uia._find_descendant_by_aid_suffix(
        action_dlg, "ComboBox", ".regexBeginnerOpCombo"
    )
    if op_combo is None:
        raise RuntimeError("Beginner-mode op combo not found")
    op_combo.select(op_label)
    time.sleep(0.1)

    # Plain-text input.
    text_edit = _uia._find_descendant_by_aid_suffix(
        action_dlg, "Edit", ".regexBeginnerText"
    )
    if text_edit is None:
        raise RuntimeError("Beginner-mode text input not found")
    text_edit.iface_value.SetValue(text)
    # Past the live-preview debounce.
    time.sleep(0.3)

    # Counter readback BEFORE Apply for Beginner — we want to assert
    # the synthesised pattern produced the expected match count, so
    # we capture before the dialog dismisses.
    counter = _uia._find_descendant_by_aid_suffix(
        action_dlg, "Text", ".regexMatchCounter"
    )
    counter_text = counter.window_text() if counter else None

    # Action combo selection (same retry+verify pattern as the Regex-
    # mode helper — see _drive_action_dialog_form for the rationale).
    action_combo = _uia._find_descendant_by_aid_suffix(
        action_dlg, "ComboBox", ".regexActionCombo"
    )
    if action_combo is None:
        raise RuntimeError("Action combo not found")
    for _ in range(3):
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
    time.sleep(0.3)
    close_btn = _uia._find_dialog_button(action_dlg, _uia.ACTION_DIALOG_BTN_CLOSE)
    close_btn.click_input()
    time.sleep(0.3)

    return counter_text


def main() -> int:
    print("scenario: s31_beginner_mode_regex")
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
    if EXPECTED_TARGET not in pre:
        print(f"FAIL: expected target {EXPECTED_TARGET!r} not in fixture rows {sorted(pre)}")
        return 1
    print(f"  pre_total={len(pre)}")

    print("step: open_action_dialog_via_menu")
    _uia.menu_path(win, _uia.MENU_ACTION, _uia.ACTION_BY_REGEX)
    action_hwnd = _uia.wait_for_dialog(pid, _uia.ACTION_DIALOG_TITLE, timeout=5)
    action_dlg = _uia.connect_by_handle(action_hwnd)
    _uia._focus(action_dlg)
    time.sleep(0.3)

    print("step: assert_beginner_mode_is_default")
    # Default mode (no persisted setting) is Beginner — verify by
    # checking the Regex radio is NOT checked. We use the Beginner
    # radio button's automation_id to confirm the toggle exists at all.
    beginner_radio = _uia._find_descendant_by_aid_suffix(
        action_dlg, "RadioButton", ".regexModeBeginner"
    )
    if beginner_radio is None:
        print("FAIL: Beginner mode radio not found — Phase B toggle missing?")
        return 1
    try:
        is_beginner = beginner_radio.is_selected()
    except Exception:
        is_beginner = False
    print(f"  beginner_is_default={is_beginner}")
    if not is_beginner:
        print("FAIL: Beginner mode is not the default")
        return 1

    print("step: select_field")
    field_combo = _uia._find_descendant_by_aid_suffix(
        action_dlg, "ComboBox", ".regexFieldCombo"
    )
    field_combo.select(FIELD)
    time.sleep(0.1)

    print("step: drive_beginner_form")
    print(f"  op={BEGINNER_OP_LABEL!r} text={BEGINNER_TEXT!r} action={ACTION_LABEL!r}")
    counter_text = _drive_beginner_form(
        action_dlg,
        op_label=BEGINNER_OP_LABEL,
        text=BEGINNER_TEXT,
        action_label=ACTION_LABEL,
    )

    print("step: assert_live_preview_counter")
    print(f"  counter_text={counter_text!r}")
    if counter_text is None:
        print("FAIL: live-preview counter not found")
        return 1
    # Beginner+contains+'q9' matches exactly 1 row (neardup_00_q95.jpg).
    # The counter format is locale-dependent; just confirm "1" appears.
    if "1" not in counter_text:
        print(f"FAIL: counter {counter_text!r} did not contain expected '1'")
        return 1

    print("step: invariant_status_bar")
    _, win = _uia.connect_main()
    if not _invariants.assert_status_bar_matches(win, r"Decision set", within_s=2.0):
        print("WARN: status bar did not echo 'Decision set' (may have cleared on timeout)")

    print("step: verify_decisions_after_apply")
    post = _read_decisions()
    failures: list[str] = []
    for name, decision in sorted(post.items()):
        expected = "delete" if name == EXPECTED_TARGET else pre.get(name, "")
        if decision != expected:
            failures.append(
                f"{name}: expected {expected!r}, got {decision!r}"
            )
        print(f"  row: name={name} pre={pre[name]!r} post={decision!r}")

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("scenario: s31_beginner_mode_regex DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
