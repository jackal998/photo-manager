"""Scenario 49 — auto-select after scan, end-to-end (#212).

Required source: ``qa/sandbox/near-duplicates`` (5 JPEGs
``neardup_00_q95.jpg`` … ``neardup_04_q65.jpg`` — same image, varying
JPEG quality. ``file_size_bytes`` is the only differentiating scoring
signal, so q95 is the deterministic per-group score winner.)

What this exercises (the production wiring that layer-1 unit tests
can't reach):

  1. The "Auto select after scan" checkbox in ScanDialog's Advanced
     Settings section is wired to ScanWorker.auto_select_enabled.
  2. When the flag is on, the worker promotes the top-scored row in
     each duplicate group to action="KEEP" BEFORE writing the manifest
     (so the decision survives into the on-disk DB and the subsequent
     manifest load).
  3. The non-top rows in the group retain their classifier actions
     (any of MOVE / EXACT / REVIEW_DUPLICATE) — auto-select picks the
     keeper and only the keeper. Marking non-top rows for deletion is
     deliberately NOT done; the user still confirms deletions
     explicitly through the existing review workflow.

The OFF case (auto-select disabled → zero KEEP rows in the manifest)
is covered by:

  * ``tests/test_scan_dialog.py::TestAutoSelectCheckbox`` — default
    state, persistence, round-trip.
  * Every other scan-driven scenario (s01, s42, s47, …) — none of
    them enables auto-select, all of them produce manifests with zero
    KEEP rows, so a regression flipping the default ON would surface
    as a cross-cutting batch failure rather than escaping unnoticed.

Companion to s42 (which pins the *score* pipeline — column populates,
within-group sort is score-DESC). This scenario pins the *decision*
auto-select makes from those scores.

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
ADVANCED_GROUP_TITLE = "Advanced settings"

# Per-#187 the near-duplicates fixture's q95 file is the highest-scoring
# row. file_size_bytes is the only differentiating signal across the
# five q-quality variants — pinning the expected winner by name is what
# proves "auto-select picks the top-scored row" end-to-end (vs. e.g.
# the first alphabetically, which would silently pass on this fixture).
EXPECTED_KEEPER = "neardup_00_q95.jpg"


def _read_manifest_actions() -> dict[str, tuple[str, str, int]]:
    """Return ``{basename: (action, user_decision, is_locked)}`` for the
    near-duplicates rows.

    Three columns matter for s49 post-#393: ``action`` (classifier
    label — top row promoted to KEEP by auto-select), ``user_decision``
    (set by the #393 keep+lock write — should be 'keep' on the
    keeper), and ``is_locked`` (the visible lock badge — 1 on the
    keeper, 0 elsewhere in non-aggressive mode).
    """
    if not MANIFEST_PATH.exists():
        raise RuntimeError(f"manifest not found at {MANIFEST_PATH}")
    conn = sqlite3.connect(str(MANIFEST_PATH))
    try:
        rows = conn.execute(
            "SELECT source_path, action, user_decision, is_locked "
            "FROM migration_manifest "
            "WHERE source_path LIKE ?",
            ("%near-duplicates%neardup_%",),
        ).fetchall()
    finally:
        conn.close()
    return {
        Path(p).name: (act, ud or "", lk)
        for p, act, ud, lk in rows
    }


def _click_advanced_group(dlg) -> None:
    """Expand the Advanced Settings collapsible groupbox.

    Qt's checkable QGroupBox surfaces in UIA as a CheckBox control
    whose title is the groupbox label. ``toggle()`` flips the check
    state, which in turn fires the ``toggled`` signal Qt uses to
    show/hide the inner content widget (#163 wiring).
    """
    grp = dlg.child_window(
        title=ADVANCED_GROUP_TITLE, control_type="CheckBox"
    )
    grp.toggle()
    time.sleep(0.2)


def _toggle_auto_select_checkbox(dlg) -> None:
    """Click the "Auto select after scan" checkbox to flip its state."""
    cb = dlg.child_window(
        title=AUTO_SELECT_CHECKBOX_TITLE, control_type="CheckBox"
    )
    cb.toggle()
    time.sleep(0.2)


def _is_auto_select_checked(dlg) -> bool:
    """Read the auto-select checkbox state via UIA toggle pattern."""
    cb = dlg.child_window(
        title=AUTO_SELECT_CHECKBOX_TITLE, control_type="CheckBox"
    )
    # ``get_toggle_state`` returns 0 (off) / 1 (on) / 2 (indeterminate).
    return cb.get_toggle_state() == 1


def main() -> int:
    print("scenario: s49_scan_auto_select")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

    failures: list[str] = []

    print("step: open_scan_dialog")
    dlg, _ = _uia.open_scan_dialog(win)

    print("step: expand_advanced_settings")
    _click_advanced_group(dlg)

    print("step: assert_checkbox_default_off")
    if _is_auto_select_checked(dlg):
        failures.append(
            "auto-select checkbox starts ON; default must be OFF"
        )
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("step: toggle_auto_select_on")
    _toggle_auto_select_checkbox(dlg)
    if not _is_auto_select_checked(dlg):
        failures.append("toggling did not turn the checkbox on")
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=60)
    print(f"  scan_elapsed_s={elapsed:.2f}")
    if "Auto-select" not in log:
        # Worker emits a one-liner naming the count of keepers; absence
        # means the auto-select branch did not run even though the flag
        # was on at scan time.
        failures.append(
            "scan log missing 'Auto-select' line — worker branch did not fire"
        )

    # run_scan_and_wait returns the instant "Done." appears in the log,
    # but ScanWorker.finished — the signal that renames the close button
    # to "Close & Load" — is queued on the main thread and may not have
    # been dispatched yet. Tiny pause closes that race so the next
    # button lookup doesn't time out.
    time.sleep(1.0)

    print("step: close_and_load")
    _uia.close_and_load_manifest(dlg)

    print("step: verify_manifest")
    rows = _read_manifest_actions()
    print(f"  manifest_rows={dict(sorted(rows.items()))}")
    # Backward-compatible action-only view for the existing assertions.
    actions = {name: tup[0] for name, tup in rows.items()}

    if EXPECTED_KEEPER not in actions:
        failures.append(
            f"expected keeper {EXPECTED_KEEPER!r} missing from manifest"
        )
    elif actions.get(EXPECTED_KEEPER) != "KEEP":
        failures.append(
            f"{EXPECTED_KEEPER}.action={actions.get(EXPECTED_KEEPER)!r}, "
            f"expected 'KEEP' (the top-scored row must be promoted)"
        )

    # Exactly one KEEP — auto-select is "top 1 per group", and this
    # fixture is one group of five files.
    keep_count = sum(1 for a in actions.values() if a == "KEEP")
    if keep_count != 1:
        failures.append(
            f"{keep_count} KEEP row(s); expected exactly 1 "
            f"(top-1-per-group on a single 5-row group)"
        )

    # The other four should retain their classifier action (any of
    # MOVE / EXACT / REVIEW_DUPLICATE). Critically: NONE of them gets
    # auto-promoted to KEEP — only the top-scored row should be.
    for name, act in actions.items():
        if name == EXPECTED_KEEPER:
            continue
        if act == "KEEP":
            failures.append(
                f"non-top row {name} has action=KEEP — only the "
                f"top-scored row should be promoted"
            )
        elif act not in ("MOVE", "EXACT", "REVIEW_DUPLICATE"):
            failures.append(
                f"{name}.action={act!r} unexpected — classifier "
                f"actions are MOVE / EXACT / REVIEW_DUPLICATE"
            )

    # #393 — keeper also receives user_decision='keep' AND is_locked=1
    # so the tree's lock badge gives a visible signal that the keeper
    # was chosen (vs the pre-#393 silent action='KEEP' which the user
    # could miss). Non-keepers stay unlocked + un-decided in the
    # non-aggressive flow.
    if EXPECTED_KEEPER in rows:
        _, ud, lk = rows[EXPECTED_KEEPER]
        if ud != "keep":
            failures.append(
                f"{EXPECTED_KEEPER}.user_decision={ud!r}, expected "
                f"'keep' (#393 keep+lock write missing)"
            )
        if lk != 1:
            failures.append(
                f"{EXPECTED_KEEPER}.is_locked={lk!r}, expected 1 "
                f"(#393 keep+lock write missing — no tree badge)"
            )
    for name, (_, ud, lk) in rows.items():
        if name == EXPECTED_KEEPER:
            continue
        if ud != "":
            failures.append(
                f"non-keeper {name}.user_decision={ud!r}, expected "
                f"'' (non-aggressive mode must not touch non-keepers)"
            )
        if lk != 0:
            failures.append(
                f"non-keeper {name}.is_locked={lk!r}, expected 0 "
                f"(only the keeper should be locked)"
            )

    # #239 — auto-select must apply VISUAL selection to the keeper row,
    # not just write action=KEEP to the manifest. The earlier soft-probe
    # incarnation (logging probe_status: XFAIL_KNOWN_BUG_239) was
    # promoted to a hard assertion when #239 fixed
    # main_window._load_manifest_after_scan to walk vm.groups for
    # action="KEEP" rows and apply the tree selection after refresh.
    print("step: verify_visual_selection_of_keeper")
    try:
        selected_basenames = _uia.read_selected_tree_row_basenames(win)
    except Exception as exc:
        # Helper isn't expected to raise (it swallows per-item errors)
        # but a top-level catch turns an unexpected exception into a
        # diagnostic FAIL instead of masking it as "selection empty".
        failures.append(
            f"reading selected tree rows raised {exc!r} — probe can't "
            f"verify #239 visual-selection state"
        )
        selected_basenames = []
    print(f"  selected_basenames={selected_basenames}")
    if selected_basenames and EXPECTED_KEEPER not in selected_basenames:
        failures.append(
            f"tree selection after scan-complete is {selected_basenames!r}; "
            f"expected to include the auto-select keeper "
            f"{EXPECTED_KEEPER!r}. action=KEEP rows must be visually "
            f"selected — see #239."
        )
    elif not selected_basenames:
        failures.append(
            f"tree selection is empty after scan-complete with "
            f"auto-select ON. Expected {EXPECTED_KEEPER!r} to be "
            f"highlighted — see #239."
        )

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("scenario: s49_scan_auto_select DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
