"""Scenario 56 — ActionDialog Apply with field=Score writes decisions (#392).

Required source: qa/sandbox/near-duplicates (5 JPEG re-saves at qualities
95/88/80/72/65 — scores cluster around 0.48–0.53 so threshold > 0.5
cleanly splits into 2 matched + 3 unchanged).

What this exercises (the production wiring that #392 surfaced as a
silent no-op before the fix):

  1. Main-window menu → Set Action by Field opens ActionDialog with
     ``match_fn`` (groups present).
  2. User picks ``field=Score``, op=``>``, threshold=``0.5``,
     action=``delete``.
  3. Dialog emits ``setActionRequested("Score", "__cmp__:>:0.5",
     "delete")``.
  4. ``file_operations.set_decision_by_regex`` dispatches the
     ``__cmp__:`` prefix to ``select_paths_by_threshold`` (the fix
     for #392 — before the fix, the pseudo-pattern was regex-compiled
     and matched as literal substring against ``str(score)`` which
     always returned 0 hits).
  5. D3 delete-confirm modal fires (Wave 10 #350); confirm.
  6. Manifest writes ``user_decision='delete'`` for the 2 rows whose
     score > 0.5.

Layer 1 pins the dispatch in
``tests/test_file_operations.py::TestSetDecisionByRegexNumericFields``
(parametrized over all 6 numeric fields). This driver pins the
UIA-observable surface end-to-end: dialog → emit → handler → manifest
write, against the live menu-route open path that was the broken
surface in #392.

Non-destructive at the filesystem level — only writes user_decision;
does NOT actually send2trash. The probe restores the fixture's
decisions to '' on cleanup.
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"

# Score field uses the localized display label "Score" which happens to
# match the internal key. If en.yml ever renames the display, this needs
# to be the LOCALIZED label (UIA combo.select() drives by visible text).
SCORE_FIELD = "Score"


def _read_fixture_decisions() -> dict[str, dict]:
    """Return {basename: {decision, score}} for the near-duplicates rows."""
    if not MANIFEST_PATH.exists():
        raise RuntimeError(f"manifest not found at {MANIFEST_PATH}")
    conn = sqlite3.connect(str(MANIFEST_PATH))
    try:
        rows = conn.execute(
            "SELECT source_path, user_decision, score "
            "FROM migration_manifest WHERE source_path LIKE ?",
            ("%near-duplicates%neardup_%",),
        ).fetchall()
    finally:
        conn.close()
    return {
        Path(p).name: {"decision": d or "", "score": float(s) if s is not None else 0.0}
        for p, d, s in rows
    }


def _reset_fixture_decisions() -> None:
    """Clear user_decision so the scenario can re-run cleanly."""
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


def _select_action_with_retry(action_combo, action_label):
    """Mirror s31's combo-flake retry."""
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
            if (action_combo.window_text() or "").strip() == action_label:
                return
        except Exception:
            pass


def main() -> int:
    print("scenario: s56_action_dialog_apply_by_score")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid}")

    print("step: open_scan_dialog")
    dlg, _ = _uia.open_scan_dialog(win)

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=30)
    print(f"  scan_elapsed_s={elapsed:.2f}")

    print("step: close_dialog")
    _uia.close_and_load_manifest(dlg)
    _, win = _uia.connect_main()

    print("step: reset_and_snapshot_pre")
    _reset_fixture_decisions()
    pre = _read_fixture_decisions()
    if len(pre) != 5:
        print(f"FAIL: expected 5 fixture rows, got {len(pre)}")
        return 1
    # Compute the expected split from real scores. >0.5 should hit
    # the highest-quality 1–2 rows; the exact count depends on the
    # scoring algorithm's current weights but the 0.48–0.53 cluster
    # means at least 1 row matches.
    expected_match = sorted(
        name for name, info in pre.items() if info["score"] > 0.5
    )
    expected_unchanged = sorted(
        name for name, info in pre.items() if info["score"] <= 0.5
    )
    for name in sorted(pre):
        print(
            f"  pre row: {name} score={pre[name]['score']:.4f} "
            f"decision={pre[name]['decision']!r}"
        )
    print(f"  expected_match={expected_match}")
    print(f"  expected_unchanged={expected_unchanged}")
    if not expected_match:
        print(
            "FAIL: no fixture rows have score>0.5 — scoring weights may "
            "have drifted; pick a threshold based on the actual pre-row "
            "scores printed above"
        )
        return 1

    print("step: open_action_dialog_via_menu")
    action_dlg, _ = _uia.open_action_by_regex_dialog(win)
    time.sleep(0.4)

    print("step: pick_score_field")
    field_combo = _uia._find_descendant_by_aid_suffix(
        action_dlg, "ComboBox", ".regexFieldCombo"
    )
    if field_combo is None:
        print("FAIL: regexFieldCombo not found")
        return 1
    field_combo.select(SCORE_FIELD)
    time.sleep(0.4)

    print("step: pick_op_greater_than")
    op_combo = _uia._find_descendant_by_aid_suffix(
        action_dlg, "ComboBox", ".numericCmpCombo"
    )
    if op_combo is None:
        print("FAIL: numericCmpCombo not found — numeric panel didn't surface")
        return 1
    op_combo.select(">")
    time.sleep(0.2)

    print("step: set_threshold_0.5")
    value_edit = _uia._find_descendant_by_aid_suffix(
        action_dlg, "Edit", ".numericValueEdit"
    )
    if value_edit is None:
        print("FAIL: numericValueEdit not found")
        return 1
    value_edit.iface_value.SetValue("0.5")
    time.sleep(0.4)  # past the live-preview debounce

    # Capture pre-Apply state — both surfaces should already agree at
    # the dialog level (preview list + match counter). The bug surfaces
    # only AFTER Apply, when the dialog says success but the manifest
    # doesn't change.
    counter = _uia._find_descendant_by_aid_suffix(
        action_dlg, "Text", ".regexMatchCounter"
    )
    counter_text_pre = (counter.window_text() if counter else "") or ""
    print(f"  counter_pre_apply={counter_text_pre!r}")
    preview_items = _uia.read_preview_items(action_dlg)
    print(f"  preview_count_pre_apply={len(preview_items)}")
    for i, it in enumerate(preview_items[:3]):
        print(f"  preview_pre[{i}]={it!r}")

    print("step: pick_delete_action")
    action_combo = _uia._find_descendant_by_aid_suffix(
        action_dlg, "ComboBox", ".regexActionCombo"
    )
    if action_combo is None:
        print("FAIL: regexActionCombo not found")
        return 1
    _select_action_with_retry(action_combo, "delete")

    print("step: click_apply")
    apply_btn = _uia._find_dialog_button(action_dlg, _uia.ACTION_DIALOG_BTN_APPLY)
    apply_btn.click_input()
    time.sleep(0.6)

    print("step: dismiss_d3_delete_confirm")
    # D3 (Wave 10 #350): delete action triggers DeleteRegexConfirmDialog.
    # Confirm to let the handler proceed and write decisions.
    d3_dismissed = _uia.drive_delete_regex_confirm(pid, confirm=True, timeout=3.0)
    print(f"  d3_confirm_dismissed={d3_dismissed}")
    if not d3_dismissed:
        print("FAIL: D3 delete-confirm did not appear — Apply may not have fired")
        return 1

    print("step: close_action_dialog")
    try:
        _uia.close_action_dialog(action_dlg)
    except Exception:
        pass

    print("step: verify_decisions_written")
    # The load-bearing assertion: manifest reflects the threshold's
    # split. Before #392's fix, post[name]['decision'] stayed '' for
    # ALL rows because the __cmp__: dispatch was missing → silent
    # no-op (the original audit-triggering bug).
    post = _read_fixture_decisions()
    failures: list[str] = []
    for name in expected_match:
        if post[name]["decision"] != "delete":
            failures.append(
                f"{name}: expected 'delete' (score={post[name]['score']:.4f}), "
                f"got {post[name]['decision']!r}"
            )
    for name in expected_unchanged:
        if post[name]["decision"] != "":
            failures.append(
                f"{name}: expected '' (score={post[name]['score']:.4f}), "
                f"got {post[name]['decision']!r}"
            )
    for name in sorted(post):
        print(
            f"  post row: {name} score={post[name]['score']:.4f} "
            f"decision={post[name]['decision']!r}"
        )

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        print(
            "FAIL: #392 regression — Apply with field=Score did not "
            "write decisions. Check file_operations.set_decision_by_regex "
            "still dispatches __cmp__: prefix correctly."
        )
        return 1

    print("step: cleanup_reset_decisions")
    _reset_fixture_decisions()

    print("scenario: s56_action_dialog_apply_by_score DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
